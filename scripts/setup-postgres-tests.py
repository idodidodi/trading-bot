"""Install test schemas ONLY into a named, loopback, disposable database."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from test_postgres_support import test_dsn
import psycopg

files=['test_auth_stub.sql','schema.sql','backtests.sql','providers.sql','sharing.sql','signal-actions.sql','followups.sql',
       'momentum.sql','family-upgrade.sql','pin-bars.sql','csv-backtests.sql',
       'asset-strategies.sql','bb-pin-bars.sql','backtest-period.sql','backtest-sorting-pagination.sql','telegram-delivery.sql',
       'telegram-routing.sql','rsi-pb.sql','postgres-runtime.sql','postgres-dashboard.sql','postgres-worker-role.sql']
with psycopg.connect(test_dsn(),autocommit=True) as db:
    if db.execute("SELECT to_regclass('public.dashboard_records')").fetchone()[0]:
        raise SystemExit('Use a NEW empty trading_bot_test database. This script does not drop databases or existing schemas.')
    for name in files:
        db.execute((Path(__file__).resolve().parent.parent/'cloud'/name).read_text(),prepare=False)
print('Disposable test schemas installed. Run python -m unittest discover -q with the same test DSN.')
