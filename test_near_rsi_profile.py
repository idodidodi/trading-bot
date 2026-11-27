"""Scoped RSI14 calculations, replay parity and retirement of queued RSI3."""
import json
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from platform_app import load_rules, validate_signal, delivery_worker
from scanner import indicators, detect, scan_once, prepare_store
from rsi_profiles import for_asset, for_symbol, superseded_alert
from test_hidden_divergence import sample
from test_postgres_support import PostgresTestCase, TestStore


class ProfileTests(unittest.TestCase):
    def test_scope_and_no_mutation(self):
        base=load_rules()
        for frame in ('15m','30m','1h','4h','daily','weekly'):
            self.assertIs(for_symbol(base,'NEARUSD',frame),base)
        for symbol in ('BTC/USD','NEAR/USDT','NEARUSDT'):
            self.assertIs(for_symbol(base,symbol,'5m'),base)
        self.assertIs(for_asset(base,{'id':'OTHER','symbol':'NEAR/USD'},'5m'),base)
        effective=for_asset(base,{'id':'NEARUSD','symbol':'NEAR/USD'},'5m')
        self.assertEqual(effective['rsi_period'],14)
        self.assertIs(for_symbol(effective,'NEARUSD','5m'),effective)
        self.assertEqual(base['rsi_period'],3)
        self.assertEqual(base['skill_hash'],'b4b8c578656c86fb')

    def test_wilder_14_uses_closes(self):
        bars,_=sample()
        closes=[100+i%3 for i in range(len(bars))]
        bars=[replace(b,close=c) for b,c in zip(bars,closes)]
        rules=for_symbol(load_rules(),'NEARUSD','5m')
        rsi,_,_=indicators(bars,rules)
        self.assertTrue(all(v is None for v in rsi[:14]))
        gains=sum(max(closes[i]-closes[i-1],0) for i in range(1,15))/14
        losses=sum(max(closes[i-1]-closes[i],0) for i in range(1,15))/14
        self.assertAlmostEqual(rsi[14],100-100/(1+gains/losses))
        self.assertEqual(indicators([replace(b,high=b.high+50,low=b.low-20) for b in bars],rules)[0],rsi)
        self.assertNotEqual(indicators(bars,load_rules())[0],rsi)

    def test_all_four_divergence_types_and_replay(self):
        from backtest import replay
        for direction in ('bullish','bearish'):
            for hidden in (False,True):
                bars,values=sample(direction,hidden)
                rules=for_symbol(load_rules(),'NEARUSD','5m')
                with patch('scanner.indicators',return_value=values) as calc:
                    signals=detect(bars,rules,'kraken:Kraken:NEAR/USD','5m',provisional=True,include_hidden=True)
                    result=replay(bars,load_rules(),'NEARUSD','5m',provisional=True,start=bars[0].start,end=bars[-1].end)
                self.assertTrue(signals)
                self.assertTrue(result['signals'])
                self.assertEqual(result['rules'],rules)
                self.assertTrue(all(c.args[1]['rsi_period']==14 for c in calc.call_args_list))
                for signal in signals:
                    self.assertEqual(signal['direction'],direction)
                    self.assertEqual(signal.get('divergence_type','regular'),'hidden' if hidden else 'regular')
                    validate_signal(signal,rules)

    def test_cloud_profile_parity(self):
        script="import {rsiRulesForSymbol} from './cloud/functions/backtests/engine.mjs';console.log(JSON.stringify(rsiRulesForSymbol("+json.dumps(load_rules())+",'kraken:Kraken:NEAR/USD','5m')));"
        result=subprocess.check_output(['node','--input-type=module','-e',script],text=True)
        self.assertEqual(json.loads(result),for_symbol(load_rules(),'NEARUSD','5m'))

    def test_superseded_scope(self):
        payload={'asset':'NEARUSD','symbol':'kraken:Kraken:NEAR/USD','timeframe':'5m','rules':load_rules()}
        self.assertTrue(superseded_alert(payload))
        self.assertFalse(superseded_alert(payload|{'timeframe':'15m'}))
        self.assertFalse(superseded_alert(payload|{'asset':'OTHER'}))
        self.assertFalse(superseded_alert(payload|{'rules':for_symbol(load_rules(),'NEARUSD','5m')}))


class ProfileDatabaseTests(PostgresTestCase):
    def test_scanner_baseline_and_one_profile_after_restart(self):
        store=TestStore();prepare_store(store)
        bars,values=sample()
        config={'timeframes':['5m'],'assets':[{'id':'NEARUSD','symbol':'NEAR/USD','provider':'csv','path':'unused','scan_strategies':['rsi']}]}
        with patch('scanner.read_csv',return_value=bars[:76]),patch('scanner.indicators',return_value=values):
            scan_once(store,config,load_rules(),bars[75].end)
        self.assertEqual(store.rows(),[])
        with patch('scanner.read_csv',return_value=bars[:77]),patch('scanner.indicators',return_value=values):
            scan_once(store,config,load_rules(),bars[76].end)
            scan_once(TestStore(),config,load_rules(),bars[76].end)
        rows=store.rows()
        self.assertTrue(rows)
        payloads=[json.loads(r[0]) for r in rows]
        self.assertTrue(all(p['rules']['rsi_period']==14 for p in payloads))
        from near_rsi_atr import annotation
        for payload in payloads:
            self.assertEqual(payload['near_rsi_atr'],annotation(config['assets'][0],bars[:77],payload)['near_rsi_atr'])
        self.assertEqual(len(payloads),len({(p['pivot1'],p['pivot2'],p['direction'],p.get('divergence_type')) for p in payloads}))

    def test_old_pending_and_retry_cancelled_without_send(self):
        store=TestStore()
        bars,values=sample()
        with patch('scanner.indicators',return_value=values):
            signal=detect(bars,load_rules(),'kraken:Kraken:NEAR/USD','5m',provisional=True,include_hidden=True)[0]
        store.insert(signal|{'asset':'NEARUSD'})
        stop=Mock();stop.is_set.side_effect=[False,True]
        with patch('platform_app.send_telegram') as send,patch('local_settings.delivery_dry_run',return_value=False):
            delivery_worker(store,'unused','unused',False,stop)
        send.assert_not_called()
        self.assertEqual(store.rows()[0][1],'cancelled')

    @patch.dict('os.environ', {'DEV_TELEGRAM_ENABLED':'true'})
    def test_rsi14_subscription_sends_once_for_repeated_comparisons(self):
        from platform_app import signal_alert_event
        from telegram_routing import deliver_routed
        store=TestStore()
        with store.connect() as db:
            db.execute("INSERT INTO telegram_asset_subscriptions VALUES('-1004484031372','NEARUSD',ARRAY['5m'],true,to_timestamp(1))")
        bars,values=sample()
        rules=for_symbol(load_rules(),'NEARUSD','5m')
        with patch('scanner.indicators',return_value=values):
            signal=detect(bars,rules,'kraken:Kraken:NEAR/USD','5m',provisional=True,include_hidden=True)[0]|{'asset':'NEARUSD'}
        from dataclasses import replace
        from near_rsi_atr import annotation
        bars=[replace(c,high=c.high+100,low=c.low-100) if c.start==signal['pivot2'] else c for c in bars]
        signal.update(annotation({'id':'NEARUSD'},bars,signal))
        self.assertTrue(signal['near_rsi_atr']['qualifies'])
        send=Mock(return_value=42)
        for offset in (0,1,2):
            comparison=signal|{'pivot1':signal['pivot1']+offset}
            deliver_routed(store,'unused','unused','RSI14',signal_alert_event(comparison),send,payload=comparison)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1],'-1004484031372')
