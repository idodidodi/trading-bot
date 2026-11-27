"""Regular/hidden qualification, replay parity, and independent alert identities."""
from dataclasses import asdict, replace
import json
import random
import subprocess
import unittest
from unittest.mock import patch
from platform_app import load_rules, validate_signal, signal_alert_event, message
from scanner import Candle, detect
from test_postgres_support import PostgresTestCase, TestStore


def sample(direction='bullish', hidden=True):
    bars = [Candle(1700000000000+i*300000,1700000000000+(i+1)*300000,100,150,50,100) for i in range(80)]
    prices = (90,92) if hidden else (90,89)
    if direction=='bearish': prices=tuple(200-v for v in prices)
    for i,p in zip((70,76),prices): bars[i]=replace(bars[i],open=p,close=p)
    rsi=[50.]*80
    rsi[70],rsi[76]=(40,30) if hidden else (40,50)
    if direction=='bearish': rsi=[100-v for v in rsi]
    return bars,(rsi,[60.]*80,[140.]*80)


class HiddenTests(unittest.TestCase):
    def test_both_directions_and_types_validate_and_label(self):
        for direction in ('bullish','bearish'):
            for hidden in (False,True):
                bars,indicators=sample(direction,hidden)
                with patch('scanner.indicators',return_value=indicators):
                    matches=detect(bars,load_rules(),'kraken:Kraken:NEAR/USD','5m',provisional=True,include_hidden=True)
                signal=next(s for s in matches if s['pivot1']==bars[70].start and s['pivot2']==bars[76].start)
                self.assertEqual(signal['direction'],direction)
                self.assertEqual(signal.get('divergence_type','regular'),'hidden' if hidden else 'regular')
                self.assertEqual(validate_signal(signal,load_rules()),signal)
                self.assertEqual('Hidden RSI' in message(signal),hidden)

    def test_hidden_api_version_cannot_match_regular_executor_allowlist(self):
        from signal_contract import signal_event
        bars,values=sample()
        rules=load_rules()
        self.assertEqual(rules['skill_hash'],'b4b8c578656c86fb')
        with patch('scanner.indicators',return_value=values):
            hidden=detect(bars,rules,'NEARUSD','5m',provisional=True,include_hidden=True)[0]
            self.assertFalse(detect(bars,rules,'NEARUSD','5m',provisional=True))
        event=signal_event('rsi',hidden)
        self.assertEqual(event['strategy_version'],'rsi-hidden-v1-'+rules['skill_hash'])
        self.assertNotEqual(event['strategy_version'],rules['skill_hash'])
        self.assertEqual(event['evidence']['divergence_type'],'hidden')

    def test_tolerance_band_touch_and_no_lookahead(self):
        bars,values=sample()
        for rsi2,allowed in ((41,True),(41.001,False)):
            values[0][76]=rsi2
            with patch('scanner.indicators',return_value=values):
                matches=detect(bars[:77],load_rules(),'NEARUSD','5m',provisional=True,include_hidden=True)
            self.assertEqual(any(s['pivot2']==bars[76].start for s in matches),allowed)
        values[0][76]=30
        bars[76]=replace(bars[76],low=61)
        with patch('scanner.indicators',return_value=values):
            self.assertFalse(detect(bars,load_rules(),'NEARUSD','5m',provisional=True,include_hidden=True))

    def test_regular_only_consumer_and_invalid_kind(self):
        bars,values=sample()
        with patch('scanner.indicators',return_value=values):
            self.assertFalse(detect(bars,load_rules(),'NEARUSD','5m',provisional=True,include_hidden=False))
            signal=detect(bars,load_rules(),'NEARUSD','5m',provisional=True,include_hidden=True)[0]
        with self.assertRaisesRegex(ValueError,'divergence type'):
            validate_signal(signal|{'divergence_type':'unknown'},load_rules())
        with self.assertRaisesRegex(ValueError,'Divergence'):
            validate_signal(signal|{'divergence_type':'regular'},load_rules())
        self.assertNotEqual(signal_alert_event(signal),signal_alert_event(signal|{'divergence_type':'regular'}))

    def test_javascript_replay_matches_python_with_real_indicators(self):
        rng=random.Random(164)
        bars=[Candle(1700000000000+i*300000,1700000000000+(i+1)*300000,p,150,50,p)
              for i in range(300) for p in [100+rng.uniform(-15,15)]]
        rules=load_rules()
        for provisional in (False,True):
            expected=detect(bars,rules,'NEARUSD','5m',provisional=provisional,include_hidden=True)
            code="import {detect} from './cloud/functions/backtests/engine.mjs';let s='';for await(const c of process.stdin)s+=c;const x=JSON.parse(s);console.log(JSON.stringify(detect(x.bars,x.rules,'NEARUSD','5m',x.provisional)));"
            actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',code],input=json.dumps(dict(bars=[asdict(b) for b in bars],rules=rules,provisional=provisional,include_hidden=True)),text=True))
            key=lambda s:(s['pivot1'],s['pivot2'],s['direction'],s.get('divergence_type','regular'))
            self.assertEqual([key(s) for s in actual],[key(s) for s in expected])
            self.assertTrue(any(s.get('divergence_type')=='hidden' for s in actual))


class HiddenPersistenceTests(PostgresTestCase):
    def test_hidden_and_regular_same_p2_do_not_suppress_each_other(self):
        from platform_app import signal_alert_delivered
        store=TestStore()
        bars,values=sample()
        with patch('scanner.indicators',return_value=values):
            hidden=detect(bars,load_rules(),'NEARUSD','5m',provisional=True,include_hidden=True)[0]
        regular=validate_signal(hidden|dict(divergence_type='regular',price2=89,rsi2=50),load_rules())
        self.assertTrue(store.insert(regular,delivery_status='sent'))
        self.assertFalse(signal_alert_delivered(store,hidden))
        self.assertTrue(signal_alert_delivered(store,regular))
        self.assertTrue(store.insert(hidden,delivery_status='sent'))
        self.assertTrue(signal_alert_delivered(store,hidden))
        self.assertFalse(store.insert(hidden))
        with store.connect() as db:
            versions={row[0] for row in db.execute("SELECT payload->>'strategy_version' FROM signal_events")}
        self.assertEqual(versions,{load_rules()['skill_hash'],'rsi-hidden-v1-'+load_rules()['skill_hash']})
