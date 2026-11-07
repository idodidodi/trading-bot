-- Additive cache owned only by the existing scanner. No execution owner or alerts.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.rsi_context_cache(
 asset text NOT NULL,source text NOT NULL,timeframe text NOT NULL,strategy_version text NOT NULL,
 last_closed_at bigint NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(asset,source,timeframe,strategy_version));
REVOKE ALL ON bot_runtime.rsi_context_cache FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON bot_runtime.rsi_context_cache TO trading_worker;
COMMIT;
