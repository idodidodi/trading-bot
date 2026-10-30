-- Apply as schema owner after postgres-runtime.sql and postgres-worker-role.sql.
-- Replaces rotating stock selection; preserves permanent portfolio configuration.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.stock_report_runs(
 id text PRIMARY KEY,day text NOT NULL,frame text NOT NULL CHECK(frame IN ('weekly','monthly')),
 state text NOT NULL,payload text NOT NULL,owner text,
 locked_until double precision NOT NULL DEFAULT 0,next_attempt double precision NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS bot_runtime.stock_report_documents(
 id text PRIMARY KEY,run_id text NOT NULL REFERENCES bot_runtime.stock_report_runs(id),
 filename text NOT NULL,strategy text,caption text NOT NULL,content bytea NOT NULL,delivered boolean NOT NULL DEFAULT false);
CREATE INDEX IF NOT EXISTS stock_report_days ON bot_runtime.stock_report_runs(day DESC);
REVOKE ALL ON bot_runtime.stock_report_runs,bot_runtime.stock_report_documents FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.stock_report_runs,bot_runtime.stock_report_documents TO trading_worker;
-- Version the managed policy exactly once. Safe to apply again.
SELECT pg_advisory_xact_lock(714269001);
WITH changed AS (
 UPDATE bot_runtime.managed_config
 SET revision=revision+1,payload=jsonb_set(payload::jsonb,'{stock_reports}','{"enabled":true}')::text
 WHERE id=1 AND NOT payload::jsonb ? 'stock_reports'
 RETURNING revision,payload
)
INSERT INTO bot_runtime.config_history(revision,payload,changed)
 SELECT revision,payload,extract(epoch FROM clock_timestamp()) FROM changed;
COMMIT;
