from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import rsi_pb
from platform_app import Store, load_rules
from scanner import Candle, prepare_store, scan_once, detect
from test_scanner import fixture


def bars(mirror=False):
    result=fixture()
    # This opposite PB closes BELOW bullish P2: structural invalidation is allowed.
    result[257]=replace(result[257],open=88,close=88.1,low=88,high=95)
    if mirror:
        result=[replace(b,open=200-b.open,high=200-b.low,low=200-b.high,close=200-b.close) for b in result]
    return result


def csv(candles):
    return 'timestamp,closed_at,open,high,low,close\n'+'\n'.join(','.join(map(str,asdict(b).values())) for b in candles)


class RSIPBTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'signals.sqlite3');prepare_store(self.store)
        self.rules=rsi_pb.load_rules()
        self.asset=dict(id='TEST',provider='csv',enabled=True,timeframes=['4h'],scan_strategies=['rsi-pb'],market='crypto')

    def scan(self,candles,**kw):
        return rsi_pb.scan(self.store,self.asset,'4h',candles,self.rules,candles[-1].end,**kw)

    def test_both_directions_ignore_structural_confirmation_ema_and_body_color(self):
        for mirror in (False,True):
            candles=bars(mirror)
            signals=rsi_pb.detect(candles,self.rules,'test','TEST','4h')
            self.assertEqual(len(signals),1)
            s=signals[0]
            self.assertEqual(s['direction'],'bearish' if mirror else 'bullish')
            self.assertEqual(s['pinbar_direction'],'bullish' if mirror else 'bearish')
            self.assertEqual(s['confirmed_at'],candles[257].end)
            self.assertEqual(s['close'],candles[257].close)
            self.assertFalse(any(x['pivot2']==candles[256].start for x in detect(candles,load_rules(),'test','4h')))
            self.assertNotIn('fast_ema',s)
            changed=candles[:];b=changed[257];changed[257]=replace(b,open=b.close,close=b.open)
            self.assertEqual(rsi_pb.detect(changed,self.rules,'test','TEST','4h')[0]['pinbar_direction'],s['pinbar_direction'])

    def test_wick_boundaries_and_doji(self):
        b=Candle(1000,2000,100,106,96.7,101) # upper 5, lower 3.3 = 66%
        self.assertEqual(rsi_pb.direction(b),'bearish')
        self.assertIsNone(rsi_pb.direction(replace(b,low=96.69)))
        self.assertIsNone(rsi_pb.direction(replace(b,high=103,low=100))) # exactly 2x body fails
        self.assertEqual(rsi_pb.direction(replace(b,open=101,low=101)),'bearish')
        self.assertIsNone(rsi_pb.direction(replace(b,open=101,low=101,high=101)))

    def test_only_next_candle_same_direction_unfinished_and_baseline(self):
        candles=bars();candles[257]=replace(candles[257],open=90,close=90.1,high=90.1,low=80)
        candles[258]=replace(candles[258],open=98,close=98.1,high=105,low=98)
        self.assertFalse(any(s['pivot2']==candles[256].start for s in rsi_pb.detect(candles,self.rules,'test','TEST','4h')))
        full=bars()
        self.assertEqual(self.scan(full)['new_signals'],0) # historical combinations never alert on activation
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_queue').fetchone()[0],0)
        other=Store(Path(self.tmp.name)/'other');prepare_store(other)
        rsi_pb.scan(other,self.asset,'4h',full[:257],self.rules,full[256].end)
        self.assertEqual(rsi_pb.scan(other,self.asset,'4h',full[:258],self.rules,full[257].end-1)['new_signals'],0)

    def test_restart_dedup_atomic_queue_and_independent_api(self):
        candles=bars();initial=self.scan(candles[:257])
        self.assertEqual(initial['waiting_setups'],1)
        self.store=Store(self.store.path)
        self.assertEqual(self.scan(candles[:258])['new_signals'],1)
        self.assertEqual(self.scan(candles[:258])['new_signals'],0)
        from dashboard_api import read
        self.assertEqual(len(read(self.store,'/api/rsi-pb')['signals']),1)
        self.assertEqual(read(self.store,'/api/pin-bars')['signals'],[])
        with self.assertRaises(ValueError):read(self.store,'/api/rsi-pb?page=-1')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_queue').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rsi_pb_pending').fetchone()[0],1) # P2+1 also diverges independently
        from cloud_sync import snapshot,batch
        snapshot(self.store)
        self.assertTrue(any(r['key']=='rsi-pb-live' for r in batch(self.store)['records']))

    def test_missing_followup_waits_for_backfill_and_catchup_is_silent(self):
        candles=bars();self.scan(candles[:257])
        missing=candles[:257]+candles[258:]
        result=self.scan(missing)
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['new_signals'],0)
        result=self.scan(candles,catchup=True)
        self.assertEqual(result['new_signals'],1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_queue').fetchone()[0],0)

    def test_atomic_rollback_when_notification_cannot_be_saved(self):
        candles=bars();self.scan(candles[:257])
        with patch('rsi_pb.message',side_effect=RuntimeError('render failed')):
            with self.assertRaises(RuntimeError):self.scan(candles[:258])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rsi_pb_signals').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rsi_pb_pending').fetchone()[0],1)
        self.assertEqual(self.scan(candles[:258])['new_signals'],1)

    def test_multiple_references_grouped_into_one_signal(self):
        candles=bars();refs=detect(candles[:257],load_rules(),'test','4h',provisional=True)
        first=next(s for s in refs if s['pivot2']==candles[256].start)
        second=first|dict(pivot1=candles[249].start,pivot1_closed_at=candles[249].end)
        with patch('scanner.detect',return_value=[first,second]):
            signals=rsi_pb.detect(candles,self.rules,'test','TEST','4h')
        self.assertEqual(len(signals),1);self.assertEqual(len(signals[0]['divergences']),2)

    def test_rotating_selection_retained_once_and_explicit_disable_cancels(self):
        candles=bars();self.asset.update(daily_selected=True)
        self.scan(candles[:257])
        config=dict(assets=[],timeframes=['4h'])
        retained=rsi_pb.retain_pending(self.store,dict(assets=[],timeframes=['4h']),config)
        self.assertTrue(retained['assets'][0]['rsi_pb_followup_only'])
        self.asset=retained['assets'][0]
        self.assertEqual(self.scan(candles[:258])['new_signals'],1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rsi_pb_pending').fetchone()[0],0)
        # Owner cancellation removes checkpoint too, so later re-enable establishes a baseline.
        self.asset.pop('rsi_pb_followup_only');self.scan(candles[:259])
        with self.store.connect() as db:rsi_pb.cancel_changed(db,[self.asset],dict(assets=[self.asset|dict(enabled=False)],timeframes=['4h']))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rsi_pb_state').fetchone()[0],0)

    def test_default_upgrade_revision_and_optout_survive_restart(self):
        import managed_assets
        legacy=dict(assets=[self.asset|dict(scan_strategies=['rsi']),self.asset|dict(id='PINS',scan_strategies=['pin-bars'])],timeframes=['4h'])
        with self.store.connect() as db:db.execute('INSERT INTO managed_config VALUES(1,7,7,?)',(json.dumps(legacy),))
        upgraded=managed_assets.get(self.store)
        self.assertEqual(upgraded['revision'],8)
        a,b=upgraded['config']['assets'];self.assertEqual(a['scan_strategies'],['rsi','rsi-pb']);self.assertEqual(b['scan_strategies'],['pin-bars'])
        a['scan_strategies']=['rsi']
        managed_assets.change(self.store,dict(action='save',revision=8,asset=a))
        self.assertEqual(managed_assets.get(Store(self.store.path))['config']['assets'][0]['scan_strategies'],['rsi'])

    def test_scanner_runs_with_standalone_rsi_disabled(self):
        candles=bars();config=dict(assets=[self.asset],timeframes=['4h'])
        with patch('scanner.read_csv',return_value=candles):
            scan_once(self.store,config,load_rules(),candles[256].end)
            row=scan_once(self.store,config,load_rules(),candles[257].end)[0]
        self.assertEqual(row['rsi_pb']['new_signals'],1)
        self.assertEqual(row['rsi_status'],'disabled')
        self.assertEqual(self.store.rows(),[])

    def test_session_calendar_and_dst_do_not_skip_missing_trading_days(self):
        ny=ZoneInfo('America/New_York')
        def bar(start,end):return Candle(int(datetime.fromisoformat(start).replace(tzinfo=ny).timestamp()*1000),int(datetime.fromisoformat(end).replace(tzinfo=ny).timestamp()*1000),100,101,99,100)
        b=bar('2026-10-30T00:00','2026-10-30T16:00')
        expected=int(datetime(2026,11,2,tzinfo=ny).timestamp()*1000)
        self.assertEqual(rsi_pb.next_start(b,'daily',dict(market='stock')),expected)
        b=bar('2026-11-25T00:00','2026-11-25T16:00')
        self.assertEqual(rsi_pb.next_start(b,'daily',dict(market='stock')),int(datetime(2026,11,27,tzinfo=ny).timestamp()*1000))
        # Session metadata may describe a different venue's open and holidays.
        intraday=Candle(1000,2000,100,101,99,100)
        self.assertEqual(rsi_pb.next_start(intraday,'4h',{'_rsi_pb_sessions':[(1000,2000),(5000,9000)]}),5000)
        self.assertEqual(rsi_pb.next_start(intraday,'1h',{'_rsi_pb_sessions':[(1000,9000)]}),2000)

    @unittest.skipUnless(shutil.which('node'),'Node.js needed for hosted calendar parity')
    def test_market_calendar_python_javascript_parity(self):
        cases=[]
        ny=ZoneInfo('America/New_York')
        for market in ('stock','forex','crypto'):
            for frame in ('1h','4h','daily','weekly','monthly'):
                for date in ('2026-03-06T00:00','2026-10-30T00:00','2026-11-25T00:00','2026-12-24T00:00'):
                    start=int(datetime.fromisoformat(date).replace(tzinfo=ny).timestamp()*1000)
                    end=start+({'1h':3600000,'4h':14400000,'daily':86400000,'weekly':7*86400000,'monthly':28*86400000})[frame]
                    b=Candle(start,end,100,101,99,100)
                    cases.append(dict(bar=asdict(b),frame=frame,market=market,expected=rsi_pb.next_start(b,frame,dict(market=market))))
        script="import {nextStart} from './cloud/functions/csv-backtests/rsi-pb-engine.mjs';console.log(JSON.stringify(JSON.parse(process.argv[1]).map(c=>nextStart(c.bar,c.frame,c.market))));"
        result=subprocess.run(['node','--input-type=module','-e',script,json.dumps(cases)],check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout),[c['expected'] for c in cases])

    @unittest.skipUnless(shutil.which('node'),'Node.js needed for hosted replay parity')
    def test_csv_python_javascript_parity_and_outcomes_start_after_pinbar(self):
        from csv_backtests import run
        for mirror in (False,True):
            candles=bars(mirror)
            for i in range(10):
                last=candles[-1];candles.append(replace(last,start=last.end,end=last.end+14400000))
            body=dict(strategy='rsi-pb',symbol='TEST',timeframe='4h',csv=csv(candles))
            report=run(body,candles[-1].end)
            script="import {replayCSV} from './cloud/functions/csv-backtests/engine.mjs';const body=JSON.parse(process.argv[1]);console.log(JSON.stringify(await replayCSV(body,Number(process.argv[2]))));"
            completed=subprocess.run(['node','--input-type=module','-e',script,json.dumps(body),str(candles[-1].end)],capture_output=True,text=True,check=True)
            remote=json.loads(completed.stdout)
            self.assertEqual(len(report['signals']),len(remote['signals']))
            for a,b in zip(report['signals'],remote['signals']):
                for key in ('direction','pinbar_direction','pivot2','candle_at','confirmed_at','open','close','body','upper_wick','lower_wick','version'):
                    self.assertEqual(a[key],b[key],key)
                self.assertEqual(a['forward'],b['forward'])
            s=report['signals'][0]
            d=1 if s['direction']=='bullish' else -1
            self.assertAlmostEqual(s['forward']['5']['directional_close_pct'],d*(candles[262].close/candles[257].close-1)*100)
        body['csv']=csv(bars()[:257])
        self.assertEqual(run(body,bars()[256].end)['signal_count'],0)


if __name__=='__main__':unittest.main()
