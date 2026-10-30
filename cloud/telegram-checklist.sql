-- Apply manually as schema owner before enabling TELEGRAM_CHECKLIST_ENABLED.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.telegram_checklist(
 id text PRIMARY KEY, bot text NOT NULL, chat text, message_id bigint,
 original text NOT NULL, done boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS bot_runtime.telegram_checklist_offsets(
 bot text PRIMARY KEY,next_update bigint NOT NULL);
REVOKE ALL ON bot_runtime.telegram_checklist,bot_runtime.telegram_checklist_offsets FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.telegram_checklist,bot_runtime.telegram_checklist_offsets TO trading_worker;
COMMIT;
