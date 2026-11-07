from datetime import datetime, timezone
from unittest.mock import patch
import json
from pathlib import Path
from test_postgres_support import PostgresTestCase, TestStore
from scanner import Candle
from rsi_context import advance, configure, read, refresh, source
from signal_service import publish, register, consume

H=3600000
ASSET=dict(id='NEARUSD',symbol='NEAR/USD',provider='kraken',exchange='Kraken',market='crypto',timeframes=['1h'])
RULES=dict(skill_hash='v1',pivot_right=1)


def bars(end=10*H,period=H):
    return [Candle(end-2*period,end-period,11,12,10,11),Candle(end-period,end,10,11,9,10)]


def finding(rows,frame='1h',**kwargs):
    return dict(symbol=source(ASSET),source=source(ASSET),market='crypto',timeframe=frame,direction='bullish',
        pivot2=rows[-1].start,confirmed_at=rows[-1].end,signal_status='provisional',version='v1',**kwargs)


class ContextTests(PostgresTestCase):
    def setUp(self):self.store=TestStore()
    def put(self,rows=None,frame='1h',now=None,findings=None,asset=None):
        rows=rows or bars()
        refresh(self.store,asset or ASSET,frame,rows,findings if findings is not None else [finding(rows,frame)],RULES,now or rows[-1].end)
    def get(self,now=10*H,asset=None,rules=None):return read(self.store,asset or ASSET,rules or RULES,now)
    def test_latest_previous_expiry_and_confirmation_never_rejuvenates(self):
        rows=bars();f=finding(rows);self.put(rows)
        first=self.get()['matches'][0]
        self.assertEqual(first['candle_position'],'latest');self.assertEqual(first['expires_at'],12*H)
        extended=rows+[Candle(10*H,11*H,12,13,11,12)]
        confirmed=f|dict(signal_status='confirmed',confirmed_at=11*H)
        self.put(extended,findings=[f,confirmed])
        matches=self.get(11*H)['matches'];self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]['candle_position'],'previous')
        self.assertEqual(matches[0]['signal_status'],'confirmed')
        self.assertEqual(matches[0]['expires_at'],12*H)
        self.assertEqual(matches[0]['structural_confirmation_at'],11*H)
        self.assertEqual(self.get(12*H)['matches'],[])
    def test_duration_rules_include_calendar_month(self):
        for frame,duration in [('1h',2*H),('4h',8*H),('daily',24*H),('weekly',168*H)]:
            asset=ASSET|dict(timeframes=[frame]);rows=bars(period=duration//(2 if frame in ('1h','4h') else 1))
            self.put(rows,frame,asset=asset)
            self.assertEqual(self.get(asset=asset)['matches'][0]['expires_at'],10*H+duration)
        at=int(datetime(2028,2,1,tzinfo=timezone.utc).timestamp()*1000)
        self.assertEqual(advance(at,'monthly')-at,29*24*H)
    def test_late_historical_analysis_does_not_make_expired_findings_fresh(self):
        self.put(now=20*H)
        self.assertEqual(self.get(20*H)['matches'],[])
    def test_invalidated_provisional_is_removed(self):
        rows=bars();f=finding(rows);self.put(rows)
        rows.append(Candle(10*H,11*H,9,10,8,9))
        self.put(rows,findings=[f]);self.assertEqual(self.get(11*H)['matches'],[])
    def test_provider_and_rule_changes_cannot_reuse_old_cache(self):
        self.put()
        self.assertEqual(self.get(asset=ASSET|dict(provider='twelvedata',exchange='Binance'))['matches'],[])
        self.assertEqual(self.get(rules=RULES|dict(skill_hash='v2'))['matches'],[])
    def test_empty_missing_and_pending_cache_are_informational(self):
        self.assertEqual(self.get()['coverage'][0]['status'],'missing')
        self.put();result=self.get(11*H)
        self.assertEqual(result['matches'],[]);self.assertEqual(result['coverage'][0]['status'],'pending_analysis')
        self.assertTrue(result['informational_only'])
        self.put(findings=[]);self.assertEqual(self.get()['matches'],[])
    def test_repeated_reads_do_not_detect_or_fetch(self):
        self.put()
        with patch('scanner.detect',side_effect=AssertionError('No reanalysis')),patch('kraken_feed.fetch_kraken',side_effect=AssertionError('No fetch')):
            self.assertEqual(self.get(),self.get())
    def test_cache_cannot_go_backwards_and_future_snapshot_is_not_used(self):
        self.put();self.put(bars(9*H),findings=[])
        self.assertEqual(len(self.get()['matches']),1)
        self.assertEqual(self.get(9*H)['matches'],[])
    def test_five_minute_event_retry_keeps_original_context_and_identity(self):
        self.put();context=self.get()
        sub=register(self.store,dict(project='context-reader',mode='replay'))
        f=finding(bars(),frame='5m')|dict(higher_timeframe_context=context)
        with self.store.connect() as db:publish(db,'rsi',f)
        f['higher_timeframe_context']=context|dict(matches=[])
        with self.store.connect() as db:publish(db,'rsi',f)
        events=consume(self.store,sub['token'])['events'];self.assertEqual(len(events),1)
        self.assertEqual(events[0]['higher_timeframe_context'],context)
        self.assertEqual(events[0]['direction'],'bullish')
    def test_missing_context_does_not_block_five_minute_event(self):
        sub=register(self.store,dict(project='empty-context',mode='replay'))
        with self.store.connect() as db:publish(db,'rsi',finding(bars(),frame='5m')|dict(higher_timeframe_context=self.get()))
        event=consume(self.store,sub['token'])['events'][0]
        self.assertEqual(event['higher_timeframe_context']['matches'],[])
    def test_default_configuration_enables_five_minute_and_hourly_kraken_rsi(self):
        config=json.loads((Path(__file__).parent/'scanner.json').read_text());updated=configure(config)
        self.assertEqual(config,updated)
        near=next(a for a in updated['assets'] if a['id']=='NEARUSD')
        self.assertIn('5m',near['timeframes']);self.assertIn('1h',near['timeframes']);self.assertIn('rsi',near['scan_strategies'])
        self.assertEqual(near['timeframe_providers']['5m'],'kraken')
    def test_scanner_generates_five_minute_signal_using_cache_without_extra_analysis(self):
        from dataclasses import replace
        from test_scanner import fixture
        from scanner import scan_once
        from platform_app import load_rules
        from signal_service import consume,register
        rules=load_rules();base=1700000000000
        rows=[replace(c,start=base+i*300000,end=base+(i+1)*300000) for i,c in enumerate(fixture())]
        config=dict(timeframes=['5m'],assets=[ASSET|dict(timeframes=['5m'],scan_strategies=['rsi'])])
        with patch('shared_kraken.fetch',return_value=rows[:256]):
            scan_once(self.store,config,rules,rows[255].end)
        project=register(self.store,dict(project='scanner-context',mode='replay'))
        import scanner
        with patch('shared_kraken.fetch',return_value=rows[:257]) as fetch,patch('scanner.detect',wraps=scanner.detect) as detect:
            scan_once(self.store,config,rules,rows[256].end)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(detect.call_count,2) # Existing confirmed + provisional scans, 5m only.
            self.assertEqual({call.args[3] for call in detect.call_args_list},{'5m'})
        events=consume(self.store,project['token'])['events']
        self.assertEqual(len(events),1);self.assertEqual(events[0]['timeframe'],'5m')
        self.assertEqual(events[0]['higher_timeframe_context']['matches'],[])
        self.assertIn('pivot2_closed_at',events[0]['evidence'])
        with patch('shared_kraken.fetch',side_effect=AssertionError('Scheduled retry must use cache')):
            scan_once(self.store,config,rules,rows[256].end)
        self.assertEqual(len(consume(self.store,project['token'])['events']),1)
    def test_normal_higher_timeframe_scan_populates_context_before_five_minute_publication(self):
        from dataclasses import replace
        from test_scanner import fixture
        from scanner import scan_once
        from platform_app import load_rules
        rules=load_rules();end=1701000000000
        histories={}
        for frame,period in [('1h',H),('5m',300000)]:
            histories[frame]=[replace(c,start=end-(257-i)*period,end=end-(256-i)*period) for i,c in enumerate(fixture()[:257])]
        config=dict(timeframes=['5m','1h'],assets=[ASSET|dict(timeframes=['5m','1h'],scan_strategies=['rsi'])])
        def fetch(asset,frame,now):return histories[frame]
        with patch('shared_kraken.fetch',side_effect=fetch):scan_once(self.store,config,rules,end-300000)
        project=register(self.store,dict(project='populated-context',mode='replay'))
        with patch('shared_kraken.fetch',side_effect=fetch) as calls:
            scan_once(self.store,config,rules,end)
            self.assertEqual([c.args[1] for c in calls.call_args_list],['1h','5m'])
        events=consume(self.store,project['token'])['events']
        event=next(e for e in events if e['timeframe']=='5m')
        matches=event['higher_timeframe_context']['matches']
        self.assertEqual(len(matches),1);self.assertEqual(matches[0]['timeframe'],'1h')
        self.assertEqual(matches[0]['direction'],'bullish');self.assertEqual(matches[0]['candle_position'],'latest')
        self.assertEqual(matches[0]['pivot_candle_closed_at'],end)
    def test_opposing_direction_is_informational_and_transaction_rollback_is_clean(self):
        rows=bars();self.put(rows,findings=[finding(rows)|dict(direction='bearish')])
        context=self.get();self.assertEqual(context['matches'][0]['direction'],'bearish')
        sub=register(self.store,dict(project='opposing-context',mode='replay'))
        signal=finding(rows,frame='5m')|dict(higher_timeframe_context=context)
        with self.assertRaises(RuntimeError):
            with self.store.connect() as db:
                publish(db,'rsi',signal)
                raise RuntimeError('rollback')
        self.assertEqual(consume(self.store,sub['token'])['events'],[])
        with self.store.connect() as db:publish(db,'rsi',signal)
        event=consume(self.store,sub['token'])['events'][0]
        self.assertEqual(event['direction'],'bullish')
        self.assertEqual(event['higher_timeframe_context']['matches'][0]['direction'],'bearish')
