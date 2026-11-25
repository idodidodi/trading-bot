import json
import unittest
from datetime import datetime
from unittest.mock import patch, Mock
from scanner import Candle
import stock_rsi_watch as w
import stock_rsi_runtime as runtime
from test_postgres_support import PostgresTestCase, TestStore


def ms(value):
    return w.millis(datetime.fromisoformat(value).replace(tzinfo=w.NY))


def signal(start, direction='bullish', first=None):
    return dict(direction=direction, pivot1=first if first is not None else start-1000000,
                pivot2=start, price1=100, price2=95, rsi1=20, rsi2=25)


def cycle(frame='weekly'):
    now = ms('2026-11-20T13:01')
    spec = next(c for c in w.cycles(now) if c['frame'] == frame)
    return w.new_cycle(spec, {}), now


class WatchTests(unittest.TestCase):
    def setup_watch(self, frame='weekly'):
        c, now = cycle(frame)
        s = w.admit(c, 'FAKE', signal(c['period_start']), now)
        return c, s, now

    def test_first_session_skipped_and_daily_weekly_overlap(self):
        self.assertEqual(w.cycles(ms('2026-11-23T14:00')), [])
        self.assertEqual({c['frame'] for c in w.cycles(ms('2026-11-20T14:00'))}, {'daily','weekly'})
        self.assertEqual([c['frame'] for c in w.cycles(ms('2026-11-24T14:00'))], ['daily'])

    def test_continuous_weekend_endpoints(self):
        fri = w.cycles(ms('2026-11-20T14:00'))
        mon = w.cycles(ms('2026-11-23T09:20'))
        self.assertEqual([c['id'] for c in mon], [c['id'] for c in fri if c['frame']=='weekly'])
        self.assertEqual(w.cycles(ms('2026-11-23T09:30')), [])

    def test_holiday_first_session_and_last_session(self):
        self.assertEqual(w.cycles(ms('2026-09-08T14:00')), [])  # Labor Day Monday
        jobs = w.cycles(ms('2026-04-02T13:00'))  # Good Friday closure
        self.assertEqual({c['frame'] for c in jobs}, {'daily','weekly'})
        self.assertEqual({c['close'] for c in jobs}, {ms('2026-04-02T16:00')})

    def test_early_close_and_dst(self):
        jobs = w.cycles(ms('2026-11-27T10:00'))
        self.assertEqual({c['close'] for c in jobs}, {ms('2026-11-27T13:00')})
        self.assertFalse(w.cycles(ms('2026-11-27T09:59')))
        self.assertEqual([c['frame'] for c in w.cycles(ms('2026-03-10T13:00'))], ['daily'])

    def test_lower_same_direction_after_watch_and_closed_ready(self):
        c,s,now = self.setup_watch()
        bar = Candle(now, now+900000,100,102,94,95)
        w.lower_observation(c,s,'15m',bar,[signal(now,'bearish')],now)
        self.assertEqual(len(c['events']),1)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now+1000)
        self.assertEqual([e['kind'] for e in c['events']], ['higher','lower'])
        w.lower_observation(c,s,'15m',bar,[signal(now)],bar.end)
        w.lower_observation(c,s,'15m',bar,[signal(now)],bar.end+1000)
        self.assertEqual([e['kind'] for e in c['events']], ['higher','lower','ready'])
        self.assertEqual(c['counts']['bullish'],1)
        self.assertEqual(c['events'][-1]['higher_status'],'PROVISIONAL')
        self.assertEqual(c['events'][-1]['lower_status'],'CLOSED')

    def test_no_retrospective_lower_match(self):
        c,s,now=self.setup_watch()
        bar=Candle(now-900000,now,100,102,94,95)
        w.lower_observation(c,s,'15m',bar,[signal(bar.start)],now)
        self.assertEqual(len(c['events']),1)

    def test_lower_disappears_only_invalid_group(self):
        c,s,now=self.setup_watch()
        bar=Candle(now,now+900000,100,102,94,95)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now)
        w.lower_observation(c,s,'15m',bar,[],bar.end)
        self.assertEqual(c['events'][-1]['chat'],w.GROUPS['invalid'])
        self.assertIn('no longer',c['events'][-1]['reason'])
        self.assertFalse(any(e['kind']=='ready' for e in c['events']))
        self.assertEqual(c['counts']['bullish'],0)

    def test_higher_closed_status_and_invalidation(self):
        c,s,now=self.setup_watch()
        bar=Candle(c['close'],c['close']+900000,100,102,94,95)
        w.recheck_parent(c,s,[s['signal']],c['close'])
        w.lower_observation(c,s,'15m',bar,[signal(bar.start)],bar.start)
        w.lower_observation(c,s,'15m',bar,[signal(bar.start)],bar.end)
        self.assertTrue(s['closed_checked'])
        self.assertEqual(c['events'][-1]['higher_status'],'CLOSED')
        c,s,now=self.setup_watch()
        w.recheck_parent(c,s,[],now)
        self.assertFalse(s['valid'])
        self.assertEqual(c['events'][-1]['chat'],w.GROUPS['invalid'])

    def test_four_per_tf_per_stock_counts_announced_candles_once(self):
        c,s,now=self.setup_watch()
        for frame in w.FRAMES:
            for n in range(6):
                start=now+n*900000
                bar=Candle(start,start+900000,100,102,94,95)
                found=[signal(start),signal(start,first=start-2000000)]
                w.lower_observation(c,s,frame,bar,found,start)
                w.lower_observation(c,s,frame,bar,found,bar.end)
        self.assertEqual(len(s['matches']),16)
        self.assertEqual(sum(e['kind']=='lower' for e in c['events']),16)
        self.assertEqual(sum(e['kind']=='ready' for e in c['events']),16)
        self.assertEqual(c['counts']['bullish'],1)

    def test_weekly_tracking_continues_after_quota_daily_stops_new(self):
        for frame in ('daily','weekly'):
            c,s,now=self.setup_watch(frame)
            c['counts']=dict(bullish=5,bearish=5)
            self.assertIsNone(w.admit(c,'NEW',signal(c['period_start']),now))
            bar=Candle(now,now+900000,100,102,94,95)
            w.lower_observation(c,s,'15m',bar,[signal(now)],now)
            self.assertEqual(len(s['matches']), int(frame=='weekly'))

    def test_invalid_parent_never_recycles_or_readies(self):
        c,s,now=self.setup_watch()
        bar=Candle(now,now+900000,100,102,94,95)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now)
        w.recheck_parent(c,s,[],now+1)
        self.assertIsNone(w.admit(c,'FAKE',signal(c['period_start']),now+2))
        w.lower_observation(c,s,'15m',bar,[signal(now)],bar.end)
        self.assertFalse(any(e['kind']=='ready' for e in c['events']))

    def test_exact_pair_and_restart(self):
        c,s,now=self.setup_watch()
        bar=Candle(now,now+900000,100,102,94,95)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now)
        c=json.loads(json.dumps(c));s=next(iter(c['setups'].values()))
        w.lower_observation(c,s,'15m',bar,[signal(now,first=now-3000000)],bar.end)
        self.assertEqual(c['events'][-1]['kind'],'invalid')

    def test_unavailable_parent_does_not_invalidate_or_validate_child(self):
        c,s,now=self.setup_watch()
        with patch('stock_rsi_data.parent_histories',side_effect=ValueError('unavailable')), patch('stock_rsi_data.lower_histories') as lower:
            runtime.update_tracking(c,now,{})
        self.assertTrue(s['valid']);lower.assert_not_called()
        self.assertEqual(len(c['events']),1)

    def test_aggregation_extended_hours_and_no_weekend_bars(self):
        from stock_rsi_data import aggregate
        bars=[Candle(ms(t),ms(t)+900000,100,102,94,95) for t in
              ['2026-11-20T04:00','2026-11-20T07:45','2026-11-20T16:00',
               '2026-11-20T19:45','2026-11-21T12:00']]
        result=aggregate(bars,'4h',bars[-1].end)
        self.assertEqual([(b.start,b.end) for b in result],
            [(ms('2026-11-20T04:00'),ms('2026-11-20T08:00')),
             (ms('2026-11-20T16:00'),ms('2026-11-20T20:00'))])


class PersistenceTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        p=patch('local_settings.delivery_dry_run',return_value=False)
        p.start();self.addCleanup(p.stop)

    def test_persist_before_send_restart_and_ledger_dedup(self):
        c,now=cycle()
        w.admit(c,'FAKE',signal(c['period_start']),now)
        runtime.save(self.store,c)
        with patch('platform_app.send_telegram',return_value=100) as send:
            runtime.flush(self.store,c,'test-token',now)
            # Simulate crash losing the per-cycle delivery flag after acceptance.
            c['events'][0]['delivered']=False
            runtime.flush(self.store,c,'test-token',now)
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[1],w.GROUPS['higher'])
        with self.store.connect() as db:
            payload=json.loads(db.execute('SELECT payload FROM stock_rsi_watches').fetchone()[0])
        self.assertTrue(payload['events'][0]['delivered'])

    def test_send_failure_preserves_event(self):
        c,now=cycle();w.admit(c,'FAKE',signal(c['period_start']),now)
        runtime.save(self.store,c)
        with patch('platform_app.send_telegram',side_effect=OSError('test failure')):
            with self.assertRaises(OSError):runtime.flush(self.store,c,'test-token',now)
        self.assertFalse(c['events'][0]['delivered'])

    def test_no_ready_when_provisional_delivery_missed(self):
        c,now=cycle();s=w.admit(c,'FAKE',signal(c['period_start']),now)
        bar=Candle(now,now+900000,100,102,94,95)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now)
        w.lower_observation(c,s,'15m',bar,[signal(now)],bar.end)
        runtime.save(self.store,c)
        with patch('platform_app.send_telegram',return_value=100) as send:
            runtime.flush(self.store,c,'test-token',bar.end)
        self.assertEqual(send.call_count,1)
        self.assertEqual(c['events'][-1]['delivered'],'lower-not-reported')

    def test_disabled_does_not_access_store(self):
        with patch.dict('os.environ',{'STOCK_RSI_WATCH_ENABLED':'false'}):
            store=Mock();runtime.worker(store,{},'',False,Mock())
        store.connect.assert_not_called()


class DataTests(unittest.TestCase):
    def test_partial_minute_dropped_and_provider_error_not_zero(self):
        from stock_rsi_data import request_bars
        now=ms('2026-11-20T13:02')
        rows=[dict(t=t,o=100,h=102,l=94,c=95) for t in
              ['2026-11-20T18:01:00Z','2026-11-20T18:02:00Z']]
        with patch('alpaca_feed.request_page',return_value={'bars':{'FAKE':rows}}):
            bars=request_bars('FAKE',1,now-60000,now)
        self.assertEqual(len(bars),1)
        self.assertEqual(bars[-1].end,now)
        with patch('alpaca_feed.request_page',side_effect=ValueError('entitlement')):
            with self.assertRaisesRegex(ValueError,'entitlement'):
                request_bars('FAKE',1,now-60000,now)

    def test_parent_cutoff_frozen_at_regular_close(self):
        from stock_rsi_data import parent_histories
        c,now=cycle()
        bar=Candle(c['period_start'],c['close'],100,102,94,95)
        with patch('stock_report_data.fetch_preclose',return_value={'FAKE':[bar]}) as fetch:
            result=parent_histories(['FAKE'],c,c['close']+3600000)
        self.assertEqual(fetch.call_args.args[1]['cutoff'],c['close'])
        self.assertEqual(result['FAKE'],[bar])

    def test_universe_missing_entitlement_retains_index_coverage(self):
        from stock_rsi_data import universe
        membership=dict(members={'FAKE':{'name':'Fake Corp'}},as_of='2026-11-20',fallback=False)
        with patch('stock_report_data.membership',return_value=membership), \
             patch('weekly_stock_screen.universe',return_value=(['MID'],['FAKE'])), \
             patch('fmp_feed.request_json',side_effect=ValueError('subscription unavailable')):
            u=universe()
        self.assertEqual(u['tiers'][0][0]['symbol'],'FAKE')
        self.assertEqual(u['tiers'][2][0]['symbol'],'MID')
        self.assertIn('Nasdaq',u['unavailable'])

    def test_universe_order_cap_and_fund_filter(self):
        from stock_rsi_data import universe
        symbols=['S'+chr(65+i//26)+chr(65+i%26) for i in range(450)]
        membership=dict(members={s:{'name':s} for s in symbols},as_of='2026-11-20',fallback=False)
        def row(s,exchange='NYSE',cap=3e9,**extra):
            return dict(symbol=s,companyName=s,marketCap=cap,exchangeShortName=exchange,
                        isEtf=False,isFund=False,isActivelyTrading=True,country='US')|extra
        rows=[row(s) for s in symbols]+[row('NAS','NASDAQ'),row('MID'),row('BIG'),
              row('SMALL','NASDAQ',1e9),row('FUND',isFund=True),row('WARRANT',companyName='Issuer Warrants')]
        with patch('stock_report_data.membership',return_value=membership), \
             patch('weekly_stock_screen.universe',return_value=(['MID'],symbols)), \
             patch('fmp_feed.request_json',return_value=rows):u=universe()
        self.assertEqual([r['symbol'] for r in u['tiers'][1]],['NAS'])
        self.assertEqual([r['symbol'] for r in u['tiers'][2]],['MID'])
        self.assertEqual([r['symbol'] for r in u['tiers'][3]],['BIG'])

    def test_no_future_candles_at_lower_close_recheck(self):
        c,now=cycle();s=w.admit(c,'FAKE',signal(c['period_start']),now)
        s['closed_checked']=True
        bar=Candle(now,now+900000,100,102,94,95)
        future=Candle(bar.end,bar.end+900000,95,120,94,119)
        w.lower_observation(c,s,'15m',bar,[signal(now)],now)
        history=[Candle(now-i*900000,now-(i-1)*900000,100,102,94,95) for i in range(250,0,-1)]+[bar,future]
        with patch('stock_rsi_data.lower_histories',return_value=({'15m':history},history,bar.end)), \
             patch('stock_rsi_runtime.signals',return_value=[signal(now)]) as detector:
            runtime.update_tracking(c,bar.end,{})
        # The pending candle's detector receives a prefix, never the next candle.
        self.assertEqual(detector.call_args_list[0].args[0][-1],bar)
        self.assertEqual(c['events'][2]['kind'],'ready')


if __name__=='__main__':unittest.main()
