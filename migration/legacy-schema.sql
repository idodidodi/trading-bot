-- SQLite schema from preserved fallback commit 1da8910. Recovery tooling ONLY.
CREATE TABLE IF NOT EXISTS paper_trades(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_status(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_routes(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,routes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_event_routes(
        event TEXT PRIMARY KEY,market TEXT,strategy TEXT,chat TEXT,general TEXT);
CREATE TABLE IF NOT EXISTS finding_feedback (
        finding_id TEXT PRIMARY KEY, source TEXT NOT NULL,
        evidence TEXT NOT NULL, rating INTEGER CHECK(rating BETWEEN 1 AND 5),
        comment TEXT NOT NULL, revision INTEGER NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feedback_history (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT NOT NULL,
        source TEXT NOT NULL, evidence TEXT NOT NULL,
        rating INTEGER CHECK(rating BETWEEN 1 AND 5), comment TEXT NOT NULL,
        revision INTEGER NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(finding_id, revision));
CREATE TABLE IF NOT EXISTS momentum_reviews(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_state(asset TEXT,timeframe TEXT,last_end INTEGER,PRIMARY KEY(asset,timeframe));
CREATE TABLE IF NOT EXISTS scanner_status(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS scan_schedule(key TEXT PRIMARY KEY,next_due INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, received REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS catchup_findings(command_id TEXT,signal_id TEXT,PRIMARY KEY(command_id,signal_id));
CREATE TABLE IF NOT EXISTS activity_log(id INTEGER PRIMARY KEY, occurred REAL, event TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS csv_backtest_runs(id TEXT PRIMARY KEY,created INTEGER,report TEXT,csv TEXT);
CREATE TABLE IF NOT EXISTS managed_config(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, applied_revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config_history(revision INTEGER PRIMARY KEY, payload TEXT NOT NULL, changed REAL NOT NULL);
CREATE TABLE IF NOT EXISTS candle_cache(feed TEXT, asset TEXT, timeframe TEXT, start INTEGER, end INTEGER, open REAL, high REAL, low REAL, close REAL, PRIMARY KEY(feed,timeframe,start));
CREATE TABLE IF NOT EXISTS local_runtime_settings(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS weekly_stock_screen(day TEXT PRIMARY KEY, week TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stock_screen_history(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS telegram_delivery(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, locked_until REAL NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 0, message_id INTEGER,
            outcome TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS backtest_jobs(id TEXT PRIMARY KEY,status TEXT,payload TEXT,updated REAL);
CREATE TABLE IF NOT EXISTS daily_universe(session TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finding_followup(finding_id TEXT PRIMARY KEY, results TEXT NOT NULL, calculated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scanner_commands(id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notification_queue(id TEXT PRIMARY KEY,text TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pin_bar_state(feed TEXT,timeframe TEXT,last_end INTEGER,PRIMARY KEY(feed,timeframe));
CREATE TABLE IF NOT EXISTS bb_pin_bar_state(feed TEXT,timeframe TEXT,last_end INTEGER,PRIMARY KEY(feed,timeframe));
CREATE TABLE IF NOT EXISTS pin_bar_signals(id TEXT PRIMARY KEY,confirmed_at INTEGER,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bb_pin_bar_signals(id TEXT PRIMARY KEY,confirmed_at INTEGER,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backtest_runs(id INTEGER PRIMARY KEY, report TEXT);
CREATE TABLE IF NOT EXISTS backtest_candle_cache(feed TEXT,timeframe TEXT,start INTEGER,end INTEGER,open REAL,high REAL,low REAL,close REAL,PRIMARY KEY(feed,timeframe,start));
CREATE TABLE IF NOT EXISTS momentum_runs(day TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS momentum_history(market TEXT,symbol TEXT,payload TEXT NOT NULL,PRIMARY KEY(market,symbol));
CREATE TABLE IF NOT EXISTS momentum_reports(id INTEGER PRIMARY KEY,created INTEGER,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS momentum_feeds(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_migrations(name TEXT PRIMARY KEY,applied REAL NOT NULL);
CREATE TABLE IF NOT EXISTS finding_state (
        finding_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('active','archived','deleted')),
        revision INTEGER NOT NULL, updated_at REAL NOT NULL);
