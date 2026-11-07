-- Manual, additive discovery provisioning. No grants enabled for existing projects.
BEGIN;
ALTER TABLE bot_runtime.signal_subscriptions ADD COLUMN IF NOT EXISTS discovery jsonb NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS signal_events_latest_discovery ON bot_runtime.signal_events(signal_id,cursor DESC);
CREATE TABLE IF NOT EXISTS bot_runtime.signal_discovery_calendars(source text PRIMARY KEY,payload jsonb NOT NULL);
REVOKE ALL ON bot_runtime.signal_discovery_calendars FROM PUBLIC,anon,authenticated;
GRANT SELECT ON bot_runtime.signal_discovery_calendars TO trading_worker;
DO $$BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='signal_api') THEN
  GRANT SELECT ON bot_runtime.managed_config,bot_runtime.scanner_status,bot_runtime.signal_discovery_calendars TO signal_api;
 END IF;
END $$;
COMMIT;
