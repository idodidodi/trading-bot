import io,json,unittest
from dataclasses import replace
from unittest.mock import patch,Mock
from scanner import Candle,scan_once,prepare_store,ProviderCandles
from platform_app import load_rules
from test_postgres_support import PostgresTestCase,TestStore
from trend_pin_bars import detect,load_rules as trend_rules
from near_alerts import needed,plan
from wyckoff_context import analyze


def sample(direction='bullish'):
    closes=[100,110,120,130,140,138,137]
    bars=[Candle(1700000000000+i*300000,1700000000000+(i+1)*300000,p-.5,p+.5,p-1,p) for i,p in enumerate(closes)]
    bars[-1]=replace(bars[-1],open=137.2,high=137.3,low=135)
    if direction=='bearish':bars=[replace(c,open=300-c.open,close=300-c.close,high=300-c.low,low=300-c.high) for c in bars]
    return bars


class EngineTests(unittest.TestCase):
    def test_symmetric_pullbacks_no_future_confirmation(self):
        for direction in ('bullish','bearish'):
            bars=sample(direction)
            results=detect(bars,trend_rules(),'kraken:Kraken:NEAR/USD','NEARUSD','5m')
            self.assertEqual(len(results),1);self.assertEqual(results[0]['direction'],direction)
            future=replace(bars[-1],start=bars[-1].end,end=bars[-1].end+300000)
            self.assertEqual(detect(bars+[future],trend_rules(),'kraken:Kraken:NEAR/USD','NEARUSD','5m')[0],results[0])
        bars=sample();bars[-1]=replace(bars[-1],low=110)
        self.assertFalse(detect(bars,trend_rules(),'source','NEARUSD','5m'))
        bars=sample();bars[5]=replace(bars[5],close=141,high=142)
        self.assertFalse(detect(bars,trend_rules(),'source','NEARUSD','5m'))

    def test_user_marked_bybit_0430_long_mixed_pullback(self):
        from pathlib import Path
        raw=json.loads((Path(__file__).parent/'tests/fixtures/near-bybit-2026-11-21-0430.json').read_text())
        bars=[Candle(int(r[0]),int(r[0])+900000,*map(float,r[1:5])) for r in raw['rows']]
        signals=detect(bars,trend_rules(),'bybit:linear:NEARUSDT','NEARUSDT','15m')
        signal=next(s for s in signals if s['candle_at']==raw['marked_start'])
        self.assertEqual(signal['direction'],'bullish')
        self.assertEqual(signal['pullback_candles'],6)
        self.assertAlmostEqual(signal['retracement'],.5538461538461548)
        self.assertEqual(signal['confirmed_at'],raw['marked_start']+900000)
        # Source candles end at the marked candle: no later rally is available.
        self.assertEqual(bars[-1].start,signal['candle_at'])

    def test_countertrend_reversals_and_indecision_are_rejected(self):
        from pathlib import Path
        cases=[('near-bybit-marked-1900.json',900000),
               ('near-bybit-marked-1345.json',900000),
               ('near-kraken-rejected-4h-2026-11-22.json',14400000)]
        for name,interval in cases:
            raw=json.loads((Path(__file__).parent/'tests/fixtures'/name).read_text())
            bars=[Candle(int(r[0]),int(r[0])+interval,*map(float,r[1:5])) for r in raw['rows']]
            mirrored=[replace(c,open=6-c.open,close=6-c.close,high=6-c.low,low=6-c.high) for c in bars]
            for history in (bars,mirrored):
                with self.subTest(name=name,mirrored=history is mirrored):
                    found=[s for s in detect(history,trend_rules(),'source','NEARUSD','4h') if s['candle_at']==raw['marked_start']]
                    self.assertFalse(found)

    def test_kraken_volume_excludes_unclosed_and_invalid(self):
        from kraken_feed import fetch_kraken
        rows=[[1700000000,'2','3','1','2','2','123.5',2],[1700000300,'2','3','1','2','2','nan',2],[1700000600,'2','3','1','2','2','900',2]]
        with patch('kraken_feed.pace_public_request'),patch('kraken_feed.urllib.request.urlopen',return_value=io.StringIO(json.dumps({'error':[],'result':{'NEAR/USD':rows,'last':1}}))):
            bars=fetch_kraken(dict(symbol='NEAR/USD',exchange='Kraken',feed_confirmed=True),'5m')
        self.assertEqual(bars.volumes,{1700000000000:123.5})

    def test_wyckoff_no_lookahead_and_missing_volume(self):
        bars=[Candle(i*300000,(i+1)*300000,100+i,102+i,99+i,101+i) for i in range(60)]
        vols={c.start:100 for c in bars}
        result=analyze(bars,vols,bars[39].end)
        self.assertEqual(result['stage'],'Markup');self.assertTrue(result['volume_available'])
        self.assertEqual(result,analyze(bars[:40],vols,bars[39].end))
        self.assertFalse(analyze(bars,{},bars[-1].end)['volume_available'])
        self.assertEqual(analyze(bars[:10],vols,bars[9].end)['stage'],'Unclear')

    def test_volume_required_for_range_stage(self):
        prior=[Candle(i*300000,(i+1)*300000,140-2*i,141-2*i,139-2*i,140-2*i) for i in range(20)]
        recent=[Candle(i*300000,(i+1)*300000,101,102,100,101.8) for i in range(20,40)]
        bars=prior+recent;volumes={c.start:10 for c in bars}
        self.assertEqual(analyze(bars,volumes,bars[-1].end)['stage'],'Accumulation')
        mirrored=[replace(c,open=300-c.open,close=300-c.close,high=300-c.low,low=300-c.high) for c in bars]
        self.assertEqual(analyze(mirrored,volumes,bars[-1].end)['stage'],'Distribution')
        self.assertEqual(analyze(bars,{},bars[-1].end)['stage'],'Unclear')

    def test_other_consumers_keep_required_checks(self):
        state=dict(preferences=[],subscriptions=[],services=[],routes={},people=[])
        with patch.dict('os.environ',{},clear=True):
            self.assertFalse(needed(state,'rsi','15m'))
            self.assertTrue(needed(state|{'services':[{'strategy':['rsi'],'symbol':['kraken:Kraken:NEAR/USD'],'timeframe':['15m']}]},'rsi','15m'))
            self.assertFalse(needed(state|{'people':[{'routes':{'crypto.rsi':'-2'},'enabled':{}}]},'rsi','15m'))
            self.assertFalse(needed(state|{'people':[{'routes':{'crypto.rsi':'-2'},'enabled':{'crypto.rsi':False}}]},'rsi','15m'))
            self.assertFalse(needed(state|{'routes':{'crypto.rsi':'-2'}},'rsi','5m'))


class LiveTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore();prepare_store(self.store)
        with self.store.connect() as db:
            db.execute("INSERT INTO telegram_asset_subscriptions VALUES('-1004484031372','NEARUSD',ARRAY['5m'],false,to_timestamp(1))")
            db.execute("INSERT INTO near_group_preferences(chat,subscriptions) VALUES('-1004484031372',%s::jsonb)",(json.dumps({'rsi':[],'trend-pin-bars':['5m']}),))
        self.asset=dict(id='NEARUSD',symbol='NEAR/USD',provider='csv',path='unused',scan_strategies=['rsi'],timeframes=['5m'])

    def test_no_subscriber_skips_fetch_and_resumption_sets_baseline(self):
        state=dict(preferences=[],subscriptions=[],services=[],routes={},people=[])
        config=dict(assets=[self.asset],timeframes=['5m'])
        bars=sample()
        with patch('near_alerts.snapshot',return_value=state),patch('scanner.read_csv') as fetch,patch.dict('os.environ',{},clear=True):
            coverage=scan_once(self.store,config,load_rules(),bars[-1].end)
            fetch.assert_not_called();self.assertTrue(all(c['status']=='not subscribed' for c in coverage))
        state['preferences']=[('-1004484031372',{'rsi':[],'trend-pin-bars':['5m']},{})]
        with patch('near_alerts.snapshot',return_value=state),patch('scanner.read_csv',return_value=bars),patch.dict('os.environ',{},clear=True):
            coverage=scan_once(self.store,config,load_rules(),bars[-1].end)
        self.assertEqual(next(c for c in coverage if c['timeframe']=='5m')['trend_pin_bars']['status'],'baseline set')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],0)

    def test_queued_reversal_is_not_delivered(self):
        from telegram_asset_subscriptions import deliver_subscriptions
        send=Mock(return_value=1)
        payload=dict(asset='NEARUSD',timeframe='5m',setup='reversal',confirmed_at=2000000)
        deliver_subscriptions(self.store,'unused','test','notification:trend-pin-bars:old',send,payload=payload)
        send.assert_not_called()
        payload['setup']='trend_pullback'
        deliver_subscriptions(self.store,'unused','test','notification:trend-pin-bars:valid',send,payload=payload)
        self.assertEqual(send.call_count,1)
        self.assertEqual(send.call_args.args[1],'-1004484031372')

    def test_near_rsi_routes_only_to_near_group_on_every_frame(self):
        from telegram_routing import deliver_routed
        frames=['5m','15m','30m','1h','4h','daily','weekly']
        with self.store.connect() as db:
            db.execute('UPDATE telegram_asset_subscriptions SET enabled=true,timeframes=%s',(frames,))
            db.execute('UPDATE near_group_preferences SET subscriptions=%s::jsonb',(json.dumps({'rsi':frames,'trend-pin-bars':frames}),))
        send=Mock(return_value=1)
        with patch('telegram_routing.select') as broad,patch('telegram_personal.deliver_personal') as personal:
            for frame in frames:
                payload=dict(asset='NEARUSD',symbol='kraken:Kraken:NEAR/USD',timeframe=frame,signal_status='provisional',alert_timing='pivot_close',confirmed_at=2000000)
                deliver_routed(self.store,'unused','-general','test','divergence:'+frame,send,payload=payload)
            broad.assert_not_called();personal.assert_not_called()
        self.assertEqual(send.call_count,7)
        self.assertEqual({c.args[1] for c in send.call_args_list},{'-1004484031372'})

    def test_trend_queue_and_opt_out_no_duplicate_delivery(self):
        import trend_pin_bars
        from telegram_routing import deliver_routed
        bars=sample();rules=trend_rules()
        # Baseline needs seven candles; prepend quiet history while retaining pattern.
        prefix=[replace(bars[0],start=bars[0].start-(10-i)*300000,end=bars[0].end-(10-i)*300000) for i in range(10)]
        bars=prefix+bars
        trend_pin_bars.scan(self.store,self.asset,'5m',bars[:-1],rules,bars[-2].end)
        result=trend_pin_bars.scan(self.store,self.asset,'5m',bars,rules,bars[-1].end)
        self.assertEqual(result['new_signals'],1)
        self.assertEqual(trend_pin_bars.scan(self.store,self.asset,'5m',bars,rules,bars[-1].end)['new_signals'],0)
        with self.store.connect() as db:
            key,text=db.execute('SELECT id,text FROM notification_queue').fetchone()
            saved=json.loads(db.execute('SELECT payload FROM trend_pin_bar_signals').fetchone()[0]);self.assertIn('wyckoff',saved)
        send=Mock(return_value=1)
        with patch('telegram_checklist.group_dismissal_enabled',return_value=False):
            for _ in range(2):deliver_routed(self.store,'unused','unused',text,'notification:'+key,send,notification_key=key)
        send.assert_called_once();self.assertEqual(send.call_args.args[1],'-1004484031372')
        with self.store.connect() as db:db.execute("UPDATE near_group_preferences SET subscriptions='{}'")
        deliver_routed(self.store,'unused','unused',text,'notification:new',send,notification_key=key)
        send.assert_called_once()
