from test_postgres_support import TestStore as Store, PostgresTestCase
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from managed_assets import ensure, get
from test_scanner import fixture

spec = importlib.util.spec_from_file_location('activate_wti', Path(__file__).parent / 'scripts/activate-wti.py')
wti = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wti)


class WtiActivationTests(PostgresTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / 'signals.sqlite3')
        self.original = dict(assets=[dict(id='USOIL', provider='csv', enabled=True,
                                        timeframes=['monthly','weekly','daily','4h'],
                                        tradingview_symbol='FOREXCOM:USOIL', unavailable_reason='Not connected'),
                                    dict(id='CL', provider='csv', enabled=False, timeframes=['daily'])])
        ensure(self.store, self.original)

    def test_validates_all_frames_and_preserves_other_assets(self):
        before = get(self.store)
        with patch.object(wti, 'fetch_tiingo', return_value=fixture()) as fetch:
            result = wti.activate(self.store)
        self.assertEqual(fetch.call_count, 4)
        saved = get(self.store)
        self.assertEqual(saved['revision'], before['revision'] + 1)
        self.assertEqual(saved['config']['assets'][1], before['config']['assets'][1])
        asset = saved['config']['assets'][0]
        self.assertEqual((asset['provider'],asset['symbol'],asset['market']), ('tiingo','USOIL/USD','commodity'))
        self.assertEqual(asset['tradingview_symbol'], '')
        self.assertNotIn('unavailable_reason', asset)
        with self.store.connect() as db:
            pending = dict(config=json.loads(db.execute('SELECT payload FROM managed_config WHERE id=1').fetchone()[0]))
        self.assertEqual(pending['config'], saved['config'])

    def test_insufficient_history_leaves_config_unchanged(self):
        before = get(self.store)
        with patch.object(wti, 'fetch_tiingo', return_value=fixture()[:10]):
            with self.assertRaisesRegex(ValueError, 'Configuration unchanged'):
                wti.activate(self.store)
        self.assertEqual(get(self.store), before)
