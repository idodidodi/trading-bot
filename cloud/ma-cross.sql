-- Apply after postgres-dashboard.sql, postgres-worker-role.sql and telegram-personal.sql.
-- No installation-specific chat IDs. Upgrade schemas before deploying the worker/UI.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.ma_cross_state(feed text,timeframe text,last_end bigint,PRIMARY KEY(feed,timeframe));
CREATE TABLE IF NOT EXISTS bot_runtime.ma_cross_signals(id text PRIMARY KEY,confirmed_at bigint,payload text NOT NULL);
CREATE INDEX IF NOT EXISTS ma_cross_confirmed ON bot_runtime.ma_cross_signals(confirmed_at DESC,id);
REVOKE ALL ON bot_runtime.ma_cross_state,bot_runtime.ma_cross_signals FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.ma_cross_state,bot_runtime.ma_cross_signals TO trading_worker;

CREATE OR REPLACE FUNCTION public.dashboard_validate_scan_strategies(asset jsonb)
RETURNS void LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
DECLARE strategies jsonb:=coalesce(asset->'scan_strategies','["rsi","pin-bars","rsi-pb","ma-cross"]'::jsonb);
BEGIN
 IF jsonb_typeof(strategies) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Choose at least one supported scan strategy';END IF;
 IF jsonb_array_length(strategies)=0
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(strategies) s WHERE s NOT IN ('rsi','pin-bars','bb-pin-bars','rsi-pb','ma-cross'))
 OR jsonb_array_length(strategies)<>(SELECT count(DISTINCT s) FROM jsonb_array_elements(strategies) s)
 THEN RAISE EXCEPTION 'Choose at least one supported scan strategy';END IF;
END $$;
REVOKE ALL ON FUNCTION public.dashboard_validate_scan_strategies(jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.telegram_route_keys() RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
 SELECT ARRAY['general','other-signals','forex.rsi','forex.pin-bars','forex.rsi-pb','crypto.rsi','crypto.pin-bars','crypto.rsi-pb','stocks.rsi','stocks.pin-bars','stocks.rsi-pb','other.rsi','other.pin-bars','other.rsi-pb'];
$$;
REVOKE ALL ON FUNCTION public.telegram_route_keys() FROM PUBLIC,anon,authenticated;

-- Preserve installed dispatcher wrappers and their authorization checks.
DO $migration$
DECLARE target regprocedure; definition text; found boolean:=false;
BEGIN
 FOR target IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname LIKE 'dashboard_api%' LOOP
  definition:=pg_get_functiondef(target);
  IF position($old$k not in ('general',$old$ IN definition)>0 THEN
   definition:=replace(definition,$old$k not in ('general',$old$,$new$k not in ('other-signals','general',$new$);
   EXECUTE definition;
   found:=true;
  ELSIF position($new$k not in ('other-signals','general',$new$ IN definition)>0 THEN found:=true;
  END IF;
 END LOOP;
 IF NOT found THEN RAISE EXCEPTION 'Telegram route validator not found';END IF;
 definition:=pg_get_functiondef('bot_runtime.api(uuid,text,jsonb)'::regprocedure);
 IF position($old$ELSIF path IN ('/api/pin-bars','/api/bb-pin-bars','/api/rsi-pb')$old$ IN definition)=0
 AND position($new$ELSIF path IN ('/api/pin-bars','/api/bb-pin-bars','/api/rsi-pb','/api/ma-cross')$new$ IN definition)=0
 THEN RAISE EXCEPTION 'Unexpected runtime API';END IF;
 definition:=replace(definition,
 $old$ELSIF path IN ('/api/pin-bars','/api/bb-pin-bars','/api/rsi-pb')$old$,
 $new$ELSIF path IN ('/api/pin-bars','/api/bb-pin-bars','/api/rsi-pb','/api/ma-cross')$new$);
 definition:=replace(definition,$old$WHEN '/api/rsi-pb' THEN 'rsi_pb' ELSE$old$,$new$WHEN '/api/rsi-pb' THEN 'rsi_pb' WHEN '/api/ma-cross' THEN 'ma_cross' ELSE$new$);
 definition:=replace(definition,$old$WHEN 'rsi_pb' THEN 'rsi_pb' ELSE$old$,$new$WHEN 'rsi_pb' THEN 'rsi_pb' WHEN 'ma_cross' THEN 'ma_cross' ELSE$new$);
 IF position($new$prefix='ma_cross'$new$ IN definition)=0 THEN
  definition:=replace(definition,$old$'rules',CASE WHEN prefix='pin_bar'$old$,
   $new$'rules',CASE WHEN prefix='ma_cross' THEN '{"fast":50,"slow":200,"timeframe":"daily"}'::jsonb WHEN prefix='pin_bar'$new$);
 END IF;
 EXECUTE definition;
 -- Default selection for newly created assets in both hosted configuration APIs.
 FOREACH target IN ARRAY ARRAY['public.dashboard_api_base(uuid,text,jsonb)'::regprocedure,'bot_runtime.asset_api(uuid,text,jsonb)'::regprocedure] LOOP
  definition:=pg_get_functiondef(target);
  definition:=replace(definition,'["rsi","pin-bars","rsi-pb"]','["rsi","pin-bars","rsi-pb","ma-cross"]');
  EXECUTE definition;
 END LOOP;
END $migration$;

-- One versioned upgrade; disabled assets remain disabled, later opt-outs survive.
DO $$
DECLARE row_value record; config jsonb; assets jsonb; asset jsonb; strategies jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(714269001);
 SELECT revision,payload::jsonb AS config INTO row_value FROM bot_runtime.managed_config WHERE id=1;
 IF FOUND AND NOT coalesce((row_value.config->>'ma_cross_defaults_applied')::boolean,false) THEN
  config:=row_value.config; assets:='[]';
  FOR asset IN SELECT value FROM jsonb_array_elements(config->'assets') LOOP
   strategies:=coalesce(asset->'scan_strategies','["rsi","pin-bars","rsi-pb"]'::jsonb);
   IF NOT strategies ? 'ma-cross' THEN strategies:=strategies||'"ma-cross"'::jsonb;END IF;
   assets:=assets||jsonb_build_array(asset||jsonb_build_object('scan_strategies',strategies));
  END LOOP;
  config:=config||jsonb_build_object('assets',assets,'ma_cross_defaults_applied',true);
  UPDATE bot_runtime.managed_config SET revision=row_value.revision+1,payload=config::text WHERE id=1;
  INSERT INTO bot_runtime.config_history VALUES(row_value.revision+1,config::text,extract(epoch FROM now()));
 END IF;
END $$;
COMMIT;
