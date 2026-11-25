-- Apply explicitly as schema owner before enabling STOCK_RSI_WATCH_ENABLED.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.stock_rsi_watches(
 id text PRIMARY KEY, expires bigint NOT NULL, payload text NOT NULL);
CREATE INDEX IF NOT EXISTS stock_rsi_watch_expiry ON bot_runtime.stock_rsi_watches(expires);
REVOKE ALL ON bot_runtime.stock_rsi_watches FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.stock_rsi_watches TO trading_worker;
COMMIT;
