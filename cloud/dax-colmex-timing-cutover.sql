-- One-time cutover, only with the existing scanner/delivery service stopped.
-- Back up these DAX rows and clear the corresponding local candle-cache series
-- before restarting. Historical delivered findings are retained as audit records.
BEGIN;
SET LOCAL search_path=bot_runtime,pg_catalog;
DO $$
DECLARE source_name constant text := 'colmex:Colmex-MT4:GER40#';
        item text;
BEGIN
  IF EXISTS (SELECT 1 FROM migration_runs WHERE snapshot_hash='colmex-dax-session-2026-11-27-v1') THEN
    RETURN;
  END IF;
  DELETE FROM candle_cache WHERE feed=source_name;
  DELETE FROM scan_state WHERE position('@'||source_name||'@' in asset)>0;
  FOREACH item IN ARRAY ARRAY['pin_bar','bb_pin_bar','rsi_pb','ma_cross'] LOOP
    EXECUTE format('DELETE FROM %I WHERE position(%L in feed)>0',item||'_state','@'||source_name||'@');
    EXECUTE format('UPDATE notification_queue SET status=''cancelled'' WHERE status=''pending'' AND id IN
      (SELECT %L||id FROM %I WHERE payload::jsonb->>''source''=%L)',
      CASE item WHEN 'pin_bar' THEN 'pin-bars:' WHEN 'bb_pin_bar' THEN 'bb-pin-bars:' WHEN 'rsi_pb' THEN 'rsi-pb:' ELSE 'ma-cross:' END,
      item||'_signals',source_name);
    EXECUTE format('UPDATE %I SET payload=(payload::jsonb || %L::jsonb)::text WHERE payload::jsonb->>''source''=%L',
      item||'_signals','{"timing_superseded":"colmex-dax-session-2026-11-27-v1"}',source_name);
  END LOOP;
  DELETE FROM rsi_pb_pending WHERE position('@'||source_name||'@' in feed)>0;
  UPDATE signals SET status=CASE WHEN status='pending' THEN 'cancelled' ELSE status END,
    payload=(payload::jsonb || '{"timing_superseded":"colmex-dax-session-2026-11-27-v1"}'::jsonb)::text
    WHERE payload::jsonb->>'source'=source_name;
  DELETE FROM scan_schedule WHERE payload::jsonb->>'provider'='colmex' AND payload::jsonb->>'symbol'='GER40#';
  INSERT INTO migration_runs(snapshot_hash,manifest) VALUES('colmex-dax-session-2026-11-27-v1',
    '{"source":"colmex:Colmex-MT4:GER40#","action":"reset candle cache and baselines; cancel unsent legacy signals; retain audit history"}');
END $$;
COMMIT;
