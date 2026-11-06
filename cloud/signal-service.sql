-- Apply before starting separated source; no historical backfill or broker writes.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.signal_stream_clock(id integer PRIMARY KEY CHECK(id=1),value bigint NOT NULL);
INSERT INTO bot_runtime.signal_stream_clock VALUES(1,0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS bot_runtime.signal_events(
 cursor bigint PRIMARY KEY,event_id text UNIQUE NOT NULL,signal_id text NOT NULL,
 published_at bigint NOT NULL,payload jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS signal_events_identity ON bot_runtime.signal_events(signal_id,cursor);
CREATE TABLE IF NOT EXISTS bot_runtime.signal_subscriptions(
 project text PRIMARY KEY,token_hash text UNIQUE NOT NULL,filters jsonb NOT NULL,
 mode text NOT NULL CHECK(mode IN ('live','replay')),created_at bigint NOT NULL,
 floor bigint NOT NULL,ack bigint NOT NULL,delivered bigint NOT NULL,enabled boolean NOT NULL DEFAULT true);
-- Subscriber credentials confer API access only, never SQL access.
REVOKE ALL ON bot_runtime.signal_events,bot_runtime.signal_subscriptions,bot_runtime.signal_stream_clock FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON bot_runtime.signal_events,bot_runtime.signal_subscriptions,bot_runtime.signal_stream_clock TO trading_worker;
COMMIT;
BEGIN;
ALTER TABLE bot_runtime.signal_subscriptions ADD COLUMN IF NOT EXISTS execution_notices boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS bot_runtime.execution_notice_receipts(project text NOT NULL,id text NOT NULL,payload_hash text NOT NULL,PRIMARY KEY(project,id));
REVOKE ALL ON bot_runtime.execution_notice_receipts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT ON bot_runtime.execution_notice_receipts TO trading_worker;
COMMIT;
