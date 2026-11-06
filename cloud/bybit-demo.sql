-- Demo execution only. Explicit policy activation follows verified source deployment.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.demo_execution_policy (
 id integer PRIMARY KEY CHECK(id=1), revision bigint NOT NULL,
 enabled boolean NOT NULL DEFAULT false, activated_at bigint NOT NULL,
 config jsonb NOT NULL, status jsonb NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS bot_runtime.demo_execution_trades (
 id text PRIMARY KEY, state text NOT NULL, payload jsonb NOT NULL,
 updated_at bigint NOT NULL);
REVOKE ALL ON bot_runtime.demo_execution_policy,bot_runtime.demo_execution_trades FROM PUBLIC,anon,authenticated;
GRANT SELECT,UPDATE ON bot_runtime.demo_execution_policy TO trading_worker;
GRANT SELECT,INSERT,UPDATE ON bot_runtime.demo_execution_trades TO trading_worker;
COMMIT;
