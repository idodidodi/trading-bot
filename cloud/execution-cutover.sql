-- MANUAL ONLY, after stopped-writer export/import verification and approval.
-- Archives old storage intact; it never drops orders or resets Telegram state.
BEGIN;
CREATE SCHEMA IF NOT EXISTS execution_legacy;
REVOKE ALL ON SCHEMA execution_legacy FROM PUBLIC,anon,authenticated,trading_worker;
DO $$
DECLARE name text;
BEGIN
 IF EXISTS(SELECT 1 FROM bot_runtime.demo_execution_policy WHERE enabled) THEN
   RAISE EXCEPTION 'Disable old demo execution and stop its owner before cutover';
 END IF;
 FOREACH name IN ARRAY ARRAY['paper_trades','paper_status','demo_execution_trades','demo_execution_policy','momentum_reviews'] LOOP
   EXECUTE format('REVOKE ALL ON bot_runtime.%I FROM PUBLIC,anon,authenticated,trading_worker',name);
   EXECUTE format('ALTER TABLE bot_runtime.%I SET SCHEMA execution_legacy',name);
 END LOOP;
END $$;
DELETE FROM bot_runtime.local_runtime_settings WHERE key='paper_trading_enabled';
-- Historical mixed simulation reports are preserved for the executor import.
CREATE TABLE execution_legacy.momentum_reports AS TABLE bot_runtime.momentum_reports;
CREATE TABLE execution_legacy.momentum_runs AS TABLE bot_runtime.momentum_runs;
REVOKE ALL ON ALL TABLES IN SCHEMA execution_legacy FROM PUBLIC,anon,authenticated,trading_worker;
-- Latest scanner replay will repopulate signal-only reports; retain detection history.
UPDATE bot_runtime.momentum_reports SET payload=(payload::jsonb - 'metrics' ||
 jsonb_build_object('metrics',jsonb_build_object('candidates',jsonb_array_length(coalesce(payload::jsonb->'signals','[]'))),
 'signals',coalesce((SELECT jsonb_agg(s - ARRAY['entry','entry_min','entry_max','stop','target','entry_open_at','expires_at','outcome','paper'] ||
 jsonb_build_object('price2',coalesce(s->'price2',s->'entry')))
 FROM jsonb_array_elements(payload::jsonb->'signals') s),'[]'),
 'note','Historical signal detection. Execution simulation archived separately.'))::text;
UPDATE bot_runtime.momentum_runs SET payload=jsonb_set(payload::jsonb,'{picks}',
 coalesce((SELECT jsonb_object_agg(k,CASE WHEN v='null'::jsonb THEN v ELSE
 v - ARRAY['entry','entry_min','entry_max','stop','target','entry_open_at','expires_at','outcome','paper'] ||
 jsonb_build_object('price2',coalesce(v->'price2',v->'entry'),
 'rules',coalesce(v->'rules','{}') - ARRAY['entry_tolerance_atr','stop_atr','target_r','max_hold','cost_bps']) END)
 FROM jsonb_each(payload::jsonb->'picks') p(k,v)),'{}'))::text;
DROP FUNCTION IF EXISTS bot_runtime.momentum_outcome(jsonb,jsonb);
DROP FUNCTION IF EXISTS bot_runtime.momentum_opened(jsonb,jsonb);
COMMIT;
