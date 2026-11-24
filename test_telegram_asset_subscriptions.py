import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from test_postgres_support import PostgresTestCase, TestStore
from telegram_asset_subscriptions import eligible, deliver_subscriptions

FRAMES=['5m','15m','30m','1h','4h','daily','weekly']


def payload(**changes):
    return dict(asset='NEARUSD',timeframe='5m',signal_status='provisional',alert_timing='pivot_close',
                confirmed_at=2000000,divergence_type='hidden',near_rsi_atr={'version':'near-5m-rsi-atr14-v1','qualifies':True})|changes


class ScopeTests(unittest.TestCase):
    def test_exact_asset_all_requested_frames_no_history_or_other_signals(self):
        sub=('5412729128','NEARUSD',FRAMES,1000)
        for frame in FRAMES:
            for kind in ('regular','hidden'):
                self.assertTrue(eligible(sub,payload(timeframe=frame,divergence_type=kind)))
        for change in (dict(asset='SOLUSD'),dict(asset='NEARUSDT'),dict(timeframe='monthly'),
                       dict(confirmed_at=999999),dict(signal_status='confirmed'),dict(alert_timing='later'),dict(asset=None)):
            self.assertFalse(eligible(sub,payload(**change)))

    def test_config_extends_existing_exact_feed_without_mutating_input(self):
        spec=importlib.util.spec_from_file_location('configure_near','scripts/configure-near-alerts.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        original=json.loads(Path('scanner.json').read_text())
        before=json.dumps(original)
        config=module.configure(original)
        near=next(a for a in config['assets'] if a['id']=='NEARUSD')
        self.assertTrue(set(FRAMES).issubset(near['timeframes']))
        self.assertTrue(set(FRAMES).issubset(near['strategy_timeframes']['rsi']))
        self.assertIn('monthly',near['strategy_timeframes']['rsi'])
        self.assertEqual(near['strategy_timeframes']['pin-bars'],next(a for a in original['assets'] if a['id']=='NEARUSD').get('timeframes',original['timeframes']))
        self.assertTrue(all(near['timeframe_providers'][f]=='kraken' for f in FRAMES))
        self.assertEqual(json.dumps(original),before)
        self.assertEqual([a for a in config['assets'] if a['id']!='NEARUSD'],[a for a in original['assets'] if a['id']!='NEARUSD'])


class DeliveryTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        with self.store.connect() as db:
            db.execute("INSERT INTO telegram_asset_subscriptions VALUES('5412729128','NEARUSD',%s,true,to_timestamp(1000))",(FRAMES,))
        self.send=Mock(return_value=123)

    def test_ordinary_five_minute_alerts_are_muted_but_large_candle_exception_sends(self):
        for kind in ('regular','hidden'):
            for direction in ('bullish','bearish'):
                event='divergence:'+kind+direction
                data=payload(divergence_type=kind,direction=direction,near_rsi_atr=None)
                deliver_subscriptions(self.store,'unused','test',event,self.send,payload=data)
        self.send.assert_not_called()
        deliver_subscriptions(self.store,'unused','test','divergence:allowed',self.send,payload=payload())
        deliver_subscriptions(self.store,'unused','test','divergence:allowed',self.send,payload=payload())
        self.assertEqual(self.send.call_count,1)

    def test_new_rsi_timeframes_do_not_expand_other_strategies(self):
        from scanner import scan_once
        from platform_app import load_rules
        from test_hidden_divergence import sample
        bars,_=sample()
        asset=dict(id='NEARUSD',provider='csv',symbol='NEAR/USD',market='crypto',enabled=True,
                   timeframes=['4h','5m'],scan_strategies=['rsi','pin-bars'],
                   strategy_timeframes={'rsi':['5m'],'pin-bars':['4h']})
        with patch('scanner.read_csv',return_value=bars):
            rows=scan_once(self.store,dict(assets=[asset],timeframes=['4h']),load_rules(),bars[-1].end)
        rows={r['timeframe']:r for r in rows}
        self.assertEqual(rows['4h']['rsi_status'],'disabled')
        self.assertEqual(rows['5m']['pin_bars']['status'],'disabled')
        self.assertEqual(rows['5m']['status'],'baseline set')

    def test_five_minute_exception_reaches_only_exact_subscription(self):
        from telegram_routing import deliver_routed
        with patch('telegram_routing.select') as select, patch('telegram_personal.deliver_personal') as personal:
            result=deliver_routed(self.store,'test-token','-general','test','divergence:near',self.send,payload=payload())
            self.assertEqual(result,'suppressed')  # broad category remains muted
            select.assert_not_called();personal.assert_not_called()
            self.send.assert_called_once()
            self.assertEqual(self.send.call_args.args[1],'5412729128')
            deliver_routed(self.store,'test-token','-general','test','divergence:other',self.send,payload=payload(asset='SOLUSD'))
            self.send.assert_called_once()

    def test_one_copy_per_destination_and_no_other_assets_or_strategies(self):
        for event,data in [('pin-bars:1',payload()),('divergence:sol',payload(asset='SOLUSD')),('divergence:old',payload(confirmed_at=999))]:
            deliver_subscriptions(self.store,'test-token','test',event,self.send,payload=data)
        self.send.assert_not_called()
        for _ in range(2):
            deliver_subscriptions(self.store,'test-token','test','divergence:near',self.send,payload=payload())
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[1],'5412729128')
        self.assertEqual(self.send.call_args.kwargs['dismissal_user_id'],5412729128)

    def test_group_copies_respect_existing_group_dismissal_setting(self):
        from telegram_checklist import group_dismissal_enabled
        with self.store.connect() as db:
            db.execute("UPDATE telegram_asset_subscriptions SET chat='-5412729128'")
        for enabled in (False,True):
            with patch('telegram_checklist.group_dismissal_enabled',return_value=enabled):
                deliver_subscriptions(self.store,'test-token','test','divergence:group:'+str(enabled),self.send,payload=payload())
            self.assertEqual(self.send.call_args.args[1],'-5412729128')
            if enabled:self.assertEqual(self.send.call_args.kwargs['dismissal_user_id'],0)
            else:self.assertNotIn('dismissal_user_id',self.send.call_args.kwargs)

    def test_failure_stays_retryable_and_disabled_subscriptions_do_not_send(self):
        from telegram_delivery import DeliveryPending
        self.send.side_effect=RuntimeError('temporary failure')
        with self.assertRaises(DeliveryPending):
            deliver_subscriptions(self.store,'test-token','test','divergence:near',self.send,payload=payload())
        with self.store.connect() as db:
            db.execute('UPDATE telegram_delivery SET locked_until=0')
        self.send.side_effect=None
        deliver_subscriptions(self.store,'test-token','test','divergence:near',self.send,payload=payload())
        self.assertEqual(self.send.call_count,2)
        with self.store.connect() as db: db.execute('UPDATE telegram_asset_subscriptions SET enabled=false')
        deliver_subscriptions(self.store,'test-token','test','divergence:next',self.send,payload=payload())
        self.assertEqual(self.send.call_count,2)

class ConfigurationTests(PostgresTestCase):
    def setUp(self):
        import os
        from test_postgres_support import test_dsn
        self.store=TestStore()
        self.environment=patch.dict(os.environ,{'TRADING_CONFIG_DATABASE_URL':test_dsn()})
        self.environment.start();self.addCleanup(self.environment.stop)
        spec=importlib.util.spec_from_file_location('configure_near','scripts/configure-near-alerts.py')
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        config=json.loads(Path('scanner.json').read_text())
        with self.store.connect() as db:
            db.execute('INSERT INTO managed_config VALUES(1,47,47,%s)',(json.dumps(config),))

    def test_atomic_apply_and_readback_without_messages(self):
        import contextlib,io
        with patch('sys.argv',['configure-near-alerts.py','--apply']),contextlib.redirect_stdout(io.StringIO()):
            self.module.main()
        with self.store.connect() as db:
            revision,applied= db.execute('SELECT revision,applied_revision FROM managed_config WHERE id=1').fetchone()
            self.assertEqual((revision,applied),(48,47))
            self.assertEqual(db.execute('SELECT chat,asset,timeframes,enabled FROM telegram_asset_subscriptions').fetchone(),
                             ('-5412729128','NEARUSD',FRAMES,True))
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_delivery').fetchone()[0],0)

    def test_broad_route_conflict_rolls_back_everything(self):
        import contextlib,io
        with self.store.connect() as db:
            db.execute("INSERT INTO public.telegram_routes(installation,revision,routes) VALUES('00000000-0000-0000-0000-000000000002',1,%s)",
                       (json.dumps({'crypto.rsi':'-5412729128'}),))
        with patch('sys.argv',['configure-near-alerts.py','--apply']),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(Exception,'broad routes'):
                self.module.main()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT revision FROM managed_config WHERE id=1').fetchone()[0],47)
            self.assertEqual(db.execute('SELECT count(*) FROM telegram_asset_subscriptions').fetchone()[0],0)

    def test_explicit_replacement_removes_only_target_group_reference(self):
        import contextlib,io
        with self.store.connect() as db:
            db.execute("INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001',8016839817,%s)",
                       (json.dumps({'chasing':'-5412729128','crypto.rsi':'-999'}),))
        with patch('sys.argv',['configure-near-alerts.py','--apply','--replace-personal-route']),contextlib.redirect_stdout(io.StringIO()):
            self.module.main()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT routes FROM public.telegram_recipients').fetchone()[0],{'crypto.rsi':'-999'})
            self.assertEqual(db.execute('SELECT chat FROM telegram_asset_subscriptions').fetchone()[0],'-5412729128')
