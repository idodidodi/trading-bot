-- Apply after postgres-dashboard.sql, postgres-worker-role.sql.
-- Does not enable a scanner, alter existing asset settings or send alerts.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.colmex_status(id integer PRIMARY KEY CHECK(id=1),payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS bot_runtime.colmex_requests(
 user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
 revision bigint NOT NULL, symbols jsonb NOT NULL CHECK(jsonb_typeof(symbols)='array'));
REVOKE ALL ON bot_runtime.colmex_status,bot_runtime.colmex_requests FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON bot_runtime.colmex_status TO trading_worker;
GRANT SELECT ON bot_runtime.colmex_requests TO trading_worker;

CREATE OR REPLACE FUNCTION bot_runtime.colmex_requested_symbols() RETURNS TABLE(symbol text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT DISTINCT jsonb_array_elements_text(r.symbols)
 FROM bot_runtime.colmex_requests r
 CROSS JOIN bot_runtime.runtime_control c
 JOIN public.dashboard_installations i ON i.id=c.installation
 WHERE c.id=1 AND (r.user_id=i.owner_id OR EXISTS(
  SELECT 1 FROM public.dashboard_members m WHERE m.installation=c.installation AND m.user_id=r.user_id));
$$;
REVOKE ALL ON FUNCTION bot_runtime.colmex_requested_symbols() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION bot_runtime.colmex_requested_symbols() TO trading_worker;

DO $$DECLARE target regprocedure; definition text;BEGIN
 FOREACH target IN ARRAY ARRAY['public.dashboard_validate_provider_asset(jsonb)'::regprocedure,
   'public.dashboard_api_base(uuid,text,jsonb)'::regprocedure,'bot_runtime.asset_api(uuid,text,jsonb)'::regprocedure] LOOP
  definition:=pg_get_functiondef(target);
  definition:=replace(definition,'''fmp'',''yahoo'')','''fmp'',''yahoo'',''colmex'')');
  definition:=replace(definition,'A-Za-z0-9_^=','A-Za-z0-9#_^=');
  IF target='public.dashboard_validate_provider_asset(jsonb)'::regprocedure THEN
   IF position('Colmex requires' in definition)=0 THEN
    definition:=replace(definition,$old$ if overrides<>'{}' and asset->>'provider'='csv' then$old$,
      $new$ if (asset->>'provider'='colmex' and exists(select 1 from jsonb_each_text(overrides) where value<>'colmex'))
        or (asset->>'provider'<>'colmex' and exists(select 1 from jsonb_each_text(overrides) where value='colmex')) then
       raise exception 'Use Colmex as a dedicated primary provider';end if;
     if overrides<>'{}' and asset->>'provider'='csv' then$new$);
    definition:=replace(definition,$old$   if provider='yahoo' then$old$,
     $new$   if provider='colmex' then
      if frame not in ('1h','4h','daily') or coalesce(asset->>'symbol','') !~ '^[A-Za-z0-9#_.-]{1,64}$'
       or asset->>'symbol' in ('.','..') or asset->>'exchange' is distinct from 'Colmex-MT4'
       or coalesce(asset->>'tradingview_symbol','')<>'' then raise exception 'Colmex requires exact symbol, Colmex-MT4 exchange and supported UTC timeframe';end if;
     end if;
     if provider='yahoo' then$new$);
   END IF;
  END IF;
  EXECUTE definition;
 END LOOP;
END $$;
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_colmex(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_colmex;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
#variable_conflict use_variable
DECLARE feed jsonb; own bot_runtime.colmex_requests; names jsonb; total integer;
BEGIN
 IF split_part(operation,'?',1)='/api/colmex' THEN
  IF auth.uid() IS NULL OR NOT public.dashboard_can_view(installation)
    OR installation IS DISTINCT FROM (SELECT r.installation FROM bot_runtime.runtime_control r WHERE id=1)
    THEN RAISE EXCEPTION 'Not authorized';END IF;
  PERFORM pg_advisory_xact_lock(714269024);
  SELECT payload INTO feed FROM bot_runtime.colmex_status WHERE id=1;
  SELECT * INTO own FROM bot_runtime.colmex_requests WHERE user_id=auth.uid();
  IF input<>'{}' THEN
   IF (input-'action'-'revision'-'symbols')<>'{}' OR input->>'action' IS DISTINCT FROM 'set'
      OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(own.revision,0))
      OR jsonb_typeof(input->'symbols') IS DISTINCT FROM 'array'
      THEN RAISE EXCEPTION 'Request changed or invalid; reload';END IF;
   names:=input->'symbols';
   IF jsonb_array_length(names)>10 OR EXISTS(SELECT 1 FROM jsonb_array_elements(names) s WHERE jsonb_typeof(s)<>'string')
      OR (SELECT count(DISTINCT s) FROM jsonb_array_elements_text(names) s)<>jsonb_array_length(names)
      THEN RAISE EXCEPTION 'Choose up to 10 distinct forex pairs';END IF;
   -- Allow removal while disconnected. New requests need a fresh actual catalogue.
   IF NOT coalesce(own.symbols,'[]') @> names THEN
    IF feed IS NULL OR feed->>'status'<>'collecting'
       OR NOT (extract(epoch FROM now())-(feed->>'updated_at')::float8 BETWEEN -5 AND 120)
       THEN RAISE EXCEPTION 'Colmex catalogue unavailable; retry when terminal is connected';END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(names) s WHERE s !~ '^[A-Za-z0-9#_.-]{1,64}$'
      OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(feed->'catalogue') c WHERE c->>'symbol'=s AND c->'export_supported'='true' AND c->'forex'='true'))
      THEN RAISE EXCEPTION 'Choose an exact forex pair from the Colmex catalogue';END IF;
   END IF;
   SELECT count(DISTINCT symbol) INTO total FROM (
    SELECT jsonb_array_elements_text(symbols) symbol FROM bot_runtime.colmex_requests WHERE user_id<>auth.uid()
    UNION ALL SELECT jsonb_array_elements_text(names)
    UNION ALL SELECT jsonb_array_elements_text(coalesce(feed->'reserved_symbols','[]'))
   ) q;
   IF total>50 THEN RAISE EXCEPTION 'Colmex shared capacity is 50 assets';END IF;
   INSERT INTO bot_runtime.colmex_requests VALUES(auth.uid(),coalesce(own.revision,0)+1,names)
     ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision,symbols=excluded.symbols;
   SELECT * INTO own FROM bot_runtime.colmex_requests WHERE user_id=auth.uid();
  END IF;
  RETURN jsonb_build_object('revision',coalesce(own.revision,0),'symbols',coalesce(own.symbols,'[]'),
    'feed',coalesce(feed,'{"status":"unavailable","catalogue":[],"reason":"Waiting for Colmex worker"}'),
    'coverage',coalesce((SELECT payload::jsonb->'coverage' FROM bot_runtime.scanner_status WHERE id=1),'[]'),
    'note','Requests collect and scan shared assets when ready; existing alert preferences still apply.');
 END IF;
 RETURN public.dashboard_api_before_colmex(installation,operation,input);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_colmex(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;
COMMIT;
