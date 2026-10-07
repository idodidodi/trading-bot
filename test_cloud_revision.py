import tempfile
from pathlib import Path
import unittest

from cloud_sync import apply, initialize
from managed_assets import ensure, get, change
from platform_app import Store


class CloudRevisionTests(unittest.TestCase):
    def test_identical_online_save_advances_local_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'signals.sqlite3')
            config = dict(assets=[dict(id='DAX-ETF', provider='alpaca', symbol='DAX',
                                      exchange='NASDAQ', feed_confirmed=True, enabled=True,
                                      timeframes=['1h'])], timeframes=['1h'])
            state = ensure(store, config)
            with store.connect() as db:
                initialize(db)
            apply(store, dict(config=dict(revision=2, applied_revision=1, config=state['config'])))
            imported = get(store)
            self.assertEqual(imported['revision'], 2)
            self.assertEqual(imported['config'], state['config'])
            saved = change(store, dict(revision=2, action='save', asset=imported['config']['assets'][0]))
            self.assertEqual(saved['revision'], 3)
