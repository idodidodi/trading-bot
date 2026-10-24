import os
from unittest.mock import patch
from test_postgres_support import TestStore as Store,PostgresTestCase
import local_settings as settings
from runtime_env import cloud_sync_enabled

class SettingsTests(PostgresTestCase):
    def setUp(self):self.store=Store()
    def test_persistent_settings_are_shared_and_sync_is_retired(self):
        with patch.dict(os.environ,TRADING_BOT_ENV='dev',TELEGRAM_BOT_TOKEN='test',TELEGRAM_CHAT_ID='1'):
            settings.save(self.store,{'telegram_enabled':True})
            self.assertTrue(settings.read(Store())['telegram_enabled'])
            with self.assertRaises(ValueError):settings.save(self.store,{'cloud_sync_enabled':True})
            self.assertFalse(cloud_sync_enabled(self.store))
    def test_development_never_places_paper_orders(self):
        with patch.dict(os.environ,TRADING_BOT_ENV='dev'):
            with self.assertRaises(ValueError):settings.save(self.store,{'paper_trading_enabled':True})
            self.assertFalse(settings.paper_trading_enabled(self.store))
    def test_production_rejects_development_delivery_overrides(self):
        with patch.dict(os.environ,TRADING_BOT_ENV='prod'):
            with self.assertRaises(ValueError):settings.save(self.store,{'telegram_enabled':False})
            self.assertTrue(settings.delivery_dry_run(self.store,True))
    def test_paper_settings_require_credentials_and_survive_restart(self):
        with patch.dict(os.environ,TRADING_BOT_ENV='prod',ALPACA_API_KEY='test',ALPACA_SECRET_KEY='test'):
            settings.save(self.store,{'paper_trading_enabled':True})
            self.assertTrue(settings.paper_trading_enabled(Store()))
