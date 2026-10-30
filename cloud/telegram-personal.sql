-- Apply after postgres-dashboard.sql and postgres-worker-role.sql.
BEGIN;
CREATE TABLE IF NOT EXISTS public.telegram_recipients(
 installation uuid NOT NULL REFERENCES public.dashboard_installations(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 telegram_user_id bigint NOT NULL, routes jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(installation,user_id));
CREATE TABLE IF NOT EXISTS public.telegram_alert_preferences(
 installation uuid NOT NULL REFERENCES public.dashboard_installations(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 revision bigint NOT NULL DEFAULT 1, enabled jsonb NOT NULL DEFAULT '{}',
 PRIMARY KEY(installation,user_id));
ALTER TABLE public.telegram_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.telegram_alert_preferences ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.telegram_recipients,public.telegram_alert_preferences FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION public.telegram_route_keys() RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
 SELECT ARRAY['general','forex.rsi','forex.pin-bars','forex.rsi-pb','crypto.rsi','crypto.pin-bars','crypto.rsi-pb','stocks.rsi','stocks.pin-bars','stocks.rsi-pb','other.rsi','other.pin-bars','other.rsi-pb'];
$$;
REVOKE ALL ON FUNCTION public.telegram_route_keys() FROM PUBLIC,anon,authenticated;
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_personal_telegram(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_personal_telegram;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
#variable_conflict use_variable
DECLARE pref public.telegram_alert_preferences; defaults jsonb; k text; v jsonb; reg jsonb;
BEGIN
 IF split_part(operation,'?',1)='/api/telegram-alerts' THEN
  IF auth.uid() IS NULL OR NOT public.dashboard_can_view(installation) THEN RAISE EXCEPTION 'Not authorized'; END IF;
  SELECT jsonb_object_agg(route,true) INTO defaults FROM unnest(public.telegram_route_keys()) route;
  -- Lock the user, including before the first preference row exists.
  PERFORM pg_advisory_xact_lock(hashtextextended(installation::text||auth.uid()::text,0));
  SELECT * INTO pref FROM public.telegram_alert_preferences p WHERE p.installation=installation AND p.user_id=auth.uid();
  IF input<>'{}' THEN
   IF jsonb_typeof(input)<>'object' OR (input-'action'-'revision'-'enabled')<>'{}'
     OR input->>'action' IS DISTINCT FROM 'save'
     OR jsonb_typeof(input->'revision') IS DISTINCT FROM 'number'
     OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(pref.revision,0))
     OR jsonb_typeof(input->'enabled') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'Settings changed or invalid; reload settings'; END IF;
   FOR k,v IN SELECT * FROM jsonb_each(input->'enabled') LOOP
    IF NOT k=ANY(public.telegram_route_keys()) OR jsonb_typeof(v)<>'boolean' THEN RAISE EXCEPTION 'Invalid alert category'; END IF;
   END LOOP;
   INSERT INTO public.telegram_alert_preferences AS p VALUES(installation,auth.uid(),1,input->'enabled')
    ON CONFLICT ON CONSTRAINT telegram_alert_preferences_pkey DO UPDATE SET enabled=excluded.enabled,revision=p.revision+1 RETURNING * INTO pref;
  END IF;
  SELECT r.routes INTO reg FROM public.telegram_recipients r WHERE r.installation=installation AND r.user_id=auth.uid();
  RETURN jsonb_build_object('revision',coalesce(pref.revision,0),'enabled',defaults||coalesce(pref.enabled,'{}'),
    'registered',reg IS NOT NULL,'groups',coalesce(reg,'{}'),'editable',true);
 END IF;
 RETURN public.dashboard_api_before_personal_telegram(installation,operation,input);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_personal_telegram(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;

CREATE TABLE IF NOT EXISTS bot_runtime.telegram_personal_events(
 user_id text NOT NULL,event text NOT NULL,chat text NOT NULL,route text NOT NULL,
 enabled boolean NOT NULL,PRIMARY KEY(user_id,event));
CREATE TABLE IF NOT EXISTS bot_runtime.telegram_personal_messages(
 id text PRIMARY KEY,bot text NOT NULL,state text NOT NULL);
CREATE TABLE IF NOT EXISTS bot_runtime.telegram_checklist_offsets(bot text PRIMARY KEY,next_update bigint NOT NULL);
CREATE OR REPLACE FUNCTION bot_runtime.personal_recipients() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('user_id',r.user_id,'telegram_user_id',r.telegram_user_id,
  'routes',r.routes,'enabled',coalesce(p.enabled,'{}'),'created_at',extract(epoch FROM r.created_at))),'[]')
 FROM public.telegram_recipients r JOIN bot_runtime.runtime_control c ON c.installation=r.installation AND c.enabled
 LEFT JOIN public.telegram_alert_preferences p ON p.installation=r.installation AND p.user_id=r.user_id;
$$;
REVOKE ALL ON bot_runtime.telegram_personal_events,bot_runtime.telegram_personal_messages FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.telegram_personal_events,bot_runtime.telegram_personal_messages TO trading_worker;
REVOKE ALL ON bot_runtime.telegram_checklist_offsets FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.telegram_checklist_offsets TO trading_worker;
REVOKE ALL ON FUNCTION bot_runtime.personal_recipients() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION bot_runtime.personal_recipients() TO trading_worker;
COMMIT;
