"""Routing persistence, recovery and actual coordinated delivery without network."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from platform_app import Store, TelegramDestinationError, send_telegram
import telegram_routing as routing


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'signals.sqlite3')
        self.routes = {key: str(-1000-i) for i, key in enumerate(routing.ROUTES)}
        routing.apply(self.store, dict(revision=1, routes=self.routes))

    def test_matrix(self):
        for market, group in [('forex','forex'), ('crypto','crypto'), ('stock','stocks'), ('stocks','stocks'), ('equity','stocks'), ('equities','stocks'), ('etf','stocks'), ('index','other'), ('commodity','other'), ('futures','other'), ('unknown','other')]:
            for strategy in ('rsi', 'pin-bars', 'bb-pin-bars', 'rsi-pb'):
                event = market+strategy
                with self.store.connect() as db:
                    routing.remember(db, event, market, strategy)
                chat, general = routing.select(self.store,event,'legacy')
                self.assertEqual(chat,self.routes[f'{group}.{strategy if strategy in ("rsi","rsi-pb") else "pin-bars"}'])
                self.assertEqual(general,self.routes['general'])
        for kind in ('market-summary', 'catchup', 'momentum'):
            self.assertEqual(routing.select(self.store,kind,'legacy')[0],self.routes['general'])

    def test_refresh_restart_and_pinned_destination(self):
        first = routing.select(self.store,'one','legacy',{'market':'crypto'})
        routing.apply(self.store,dict(revision=2,routes={'general':'-5','crypto.rsi':'-6'}))
        restarted=Store(self.store.path)
        self.assertEqual(routing.read(restarted)['revision'],2)
        self.assertEqual(routing.select(restarted,'one','legacy'),first)
        self.assertEqual(routing.select(restarted,'two','legacy',{'market':'crypto'})[0],'-6')
        routing.apply(restarted,dict(revision=1,routes=self.routes))
        self.assertEqual(routing.read(restarted)['revision'],2)
        with patch('telegram_routing.fetch_cloud',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):routing.refresh(restarted)
        self.assertEqual(routing.read(restarted)['revision'],2)

    def test_validation_is_atomic(self):
        for routes in ({'bad':'123'},{'general':123},{'general':'@name'},{'general':'1.2'},{'general':' 123'}):
            with self.assertRaises(ValueError):routing.apply(self.store,dict(revision=2,routes=routes))
            self.assertEqual(routing.read(self.store)['revision'],1)
        self.assertEqual(routing.validate({'general':'-100123'})['general'],'-100123')

    def test_missing_route_and_unrecoverable_legacy_use_general(self):
        routing.apply(self.store,dict(revision=2,routes={'general':'-5'}))
        self.assertEqual(routing.select(self.store,'unknown','legacy',{'market':'forex'})[0],'-5')
        self.assertEqual(routing.select(self.store,'old','legacy',{'symbol':'unknown'})[0],'-5')
        fresh=Store(Path(self.tmp.name)/'fresh.sqlite3')
        self.assertEqual(routing.select(fresh,'old','-7')[0],'-7')

    def test_legacy_recovery_from_assets_and_both_strategy_tables(self):
        asset={'id':'EURUSD','market':'forex','provider':'oanda','symbol':'EUR/USD','exchange':'OANDA'}
        with self.store.connect() as db:
            db.execute('INSERT INTO managed_config VALUES(1,1,1,?)',(json.dumps({'assets':[asset]}),))
            routing.remember(db,'rsi',None,'rsi')
            for strategy, table in [('pin-bars','pin_bar_signals'),('bb-pin-bars','bb_pin_bar_signals')]:
                db.execute(f'CREATE TABLE {table}(id TEXT,payload TEXT)')
                db.execute(f'INSERT INTO {table} VALUES(?,?)',('old',json.dumps({'symbol':'EURUSD'})))
        self.assertEqual(routing.select(self.store,'rsi','legacy',{'symbol':'oanda:OANDA:EUR/USD'})[0],self.routes['forex.rsi'])
        for strategy in ('pin-bars','bb-pin-bars'):
            self.assertEqual(routing.select(self.store,strategy,'legacy',notification_key=strategy+':old')[0],self.routes['forex.pin-bars'])

    def test_alpaca_unknown_market_recovers_stock_and_equity_alias_routes(self):
        assets = [dict(id='RDW', symbol='RDW', provider='alpaca', exchange='Alpaca-sip-split'),
                  dict(id='GLW', symbol='GLW', provider='alpaca', exchange='Alpaca-sip-split', market='equity')]
        with self.store.connect() as db:
            db.execute('INSERT INTO managed_config VALUES(1,1,1,?)', (json.dumps({'assets': assets}),))
        for symbol, market in [('RDW', 'unknown'), ('GLW', 'equity')]:
            payload = dict(symbol='alpaca:Alpaca-sip-split:'+symbol, market=market)
            with self.store.connect() as db:
                routing.remember(db, symbol, market, 'rsi')
            self.assertEqual(routing.select(self.store, symbol, 'legacy', payload)[0], self.routes['stocks.rsi'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT market FROM telegram_event_routes WHERE event='RDW'").fetchone()[0], 'stock')
            db.execute('CREATE TABLE pin_bar_signals(id TEXT,payload TEXT)')
            db.execute('INSERT INTO pin_bar_signals VALUES(?,?)', ('rdw', json.dumps({'symbol':'alpaca:Alpaca-sip-split:RDW','market':'unknown'})))
            routing.remember(db, 'pin', 'unknown', 'pin-bars')
        self.assertEqual(routing.select(self.store, 'pin', 'legacy', notification_key='pin-bars:rdw')[0], self.routes['stocks.pin-bars'])

    def test_selected_destination_stays_pinned_after_classification_recovery(self):
        with self.store.connect() as db:
            routing.remember(db, 'started', 'unknown', 'rsi')
        original = routing.select(self.store, 'started', 'legacy', {'market':'unknown'})
        self.assertEqual(original[0], self.routes['other.rsi'])
        self.assertEqual(routing.select(self.store, 'started', 'legacy',
            {'symbol':'alpaca:Alpaca-sip-split:RDW','market':'unknown'}), original)

    def coordinate(self, payload):
        return {'status':{'acquire':'acquired','renew':'acquired','complete':'completed','release':'released'}[payload['action']], 'silent':False}

    def test_destination_rejection_falls_back_and_retries_general_after_restart(self):
        sender=Mock(side_effect=[TelegramDestinationError('unavailable'),RuntimeError('offline')])
        with patch('telegram_delivery.coordinator',side_effect=self.coordinate):
            with self.assertRaises(RuntimeError):
                routing.deliver_routed(self.store,'123:secret','legacy','text','event',sender,payload={'market':'forex'})
            self.assertEqual([c.args[1] for c in sender.call_args_list],[self.routes['forex.rsi'],self.routes['general']])
            sender=Mock(return_value=42)
            self.assertEqual(routing.deliver_routed(Store(self.store.path),'123:secret','legacy','text','event',sender),'sent')
            self.assertEqual(sender.call_args.args[1],self.routes['general'])

    def test_ambiguous_failure_never_falls_back_and_other_groups_continue(self):
        sender=Mock(side_effect=RuntimeError('timeout'))
        with patch('telegram_delivery.coordinator',side_effect=self.coordinate):
            with self.assertRaises(RuntimeError):
                routing.deliver_routed(self.store,'123:secret','legacy','text','event',sender,payload={'market':'crypto'})
            sender.assert_called_once()
            other=Mock(return_value=42)
            self.assertEqual(routing.deliver_routed(self.store,'123:secret','legacy','text','other',other,payload={'market':'stock'}),'sent')
            self.assertEqual(other.call_args.args[1],self.routes['stocks.rsi'])
        self.assertEqual(routing.select(self.store,'event','legacy')[0],self.routes['crypto.rsi'])

    def test_real_sender_classifies_only_definitive_destination_errors(self):
        for code, reason, expected in [(400,'Bad Request: chat not found',TelegramDestinationError), (403,'Forbidden: bot was kicked from the supergroup chat',TelegramDestinationError),(400,'Bad Request: message is too long',RuntimeError),(429,'Too Many Requests',RuntimeError),(500,'Internal Server Error',RuntimeError)]:
            error=urllib.error.HTTPError('https://telegram.invalid',code,'error',{},io.BytesIO(json.dumps({'description':reason}).encode()))
            with patch('platform_app.urllib.request.urlopen',side_effect=error):
                with self.assertRaises(expected) as caught:send_telegram('123:secret','-1','text',source_identity={'device':'test','environment':'dev','sender_id':'x'})
                if expected is RuntimeError:self.assertNotIsInstance(caught.exception,TelegramDestinationError)
            error.close()

    def test_read_only_cloud_request_and_disabled_upload_refresh(self):
        response=io.BytesIO(json.dumps({'telegram_routing':{'revision':2,'routes':{'general':'-8'}}}).encode())
        with patch.dict(os.environ,{'CLOUD_SYNC_URL':'https://cloud.invalid/sync','CLOUD_SYNC_TOKEN':'private'}), patch('telegram_routing.urllib.request.urlopen',return_value=response) as fetch:
            routing.refresh(self.store)
        self.assertEqual(json.loads(fetch.call_args.args[0].data),{'routing_only':True})
        self.assertEqual(routing.read(self.store)['routes']['general'],'-8')
        import cloud_sync
        stop=Mock();stop.is_set.side_effect=[False,True];stop.wait.return_value=True
        with patch.dict(os.environ,{'CLOUD_SYNC_URL':'https://cloud.invalid/sync','CLOUD_SYNC_TOKEN':'private'}), patch('runtime_env.cloud_sync_enabled',return_value=False), patch('telegram_routing.refresh') as refresh, patch('cloud_sync.snapshot') as snapshot:
            cloud_sync.worker(self.store,stop)
        refresh.assert_called_once_with(self.store)
        snapshot.assert_not_called()

    def test_cli_routes_do_not_send_and_targeted_tests_use_cloud_cache(self):
        import contextlib
        import telegram_setup
        with patch('telegram_setup.load_env'), patch('runtime_env.data_directory',return_value=Path(self.tmp.name)), patch('telegram_routing.refresh'), patch('telegram_setup.send_telegram') as send, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch('sys.argv',['telegram_setup.py','routes']):telegram_setup.main()
            send.assert_not_called()
            self.assertIn('forex.rsi: '+self.routes['forex.rsi'],output.getvalue())
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'123:secret'}), patch('sys.argv',['telegram_setup.py','test','--route','forex.rsi']):telegram_setup.main()
            self.assertEqual(send.call_args.args[1],self.routes['forex.rsi'])
            send.reset_mock()
            with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'123:secret'}), patch('sys.argv',['telegram_setup.py','test','--all']):telegram_setup.main()
            self.assertEqual(send.call_count,len(routing.ROUTES))

    def test_cloud_general_can_enable_delivery_without_local_chat_id(self):
        import local_settings
        with patch.dict(os.environ,{'TRADING_BOT_ENV':'dev','TELEGRAM_BOT_TOKEN':'123:secret','TELEGRAM_CHAT_ID':''}):
            self.assertTrue(local_settings.save(self.store,{'telegram_enabled':True})['telegram_enabled'])
