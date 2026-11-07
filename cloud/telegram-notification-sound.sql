-- Apply after telegram-personal.sql and existing dashboard migrations, before UI/worker publication.
-- Idempotent wrapper preserves other installed API routes. No recipient registration or test sends.
BEGIN;
ALTER TABLE public.telegram_alert_preferences ADD COLUMN IF NOT EXISTS silent jsonb NOT NULL DEFAULT '{}';
ALTER TABLE public.telegram_alert_preferences ADD COLUMN IF NOT EXISTS quiet_hours jsonb NOT NULL DEFAULT '{"enabled":false,"start":"22:00","end":"07:00","timezone":"UTC"}';
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_notification_sound(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_notification_sound;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
#variable_conflict use_variable
DECLARE pref public.telegram_alert_preferences; defaults jsonb; k text; v jsonb; reg jsonb; sound jsonb; quiet jsonb;
BEGIN
 IF split_part(operation,'?',1)='/api/telegram-alerts' THEN
  IF auth.uid() IS NULL OR NOT public.dashboard_can_view(installation) THEN RAISE EXCEPTION 'Not authorized'; END IF;
  SELECT jsonb_object_agg(route,true) INTO defaults FROM unnest(public.telegram_route_keys()) route;
  -- Lock the user, including before the first preference row exists.
  PERFORM pg_advisory_xact_lock(hashtextextended(installation::text||auth.uid()::text,0));
  SELECT * INTO pref FROM public.telegram_alert_preferences p WHERE p.installation=installation AND p.user_id=auth.uid();
  IF input<>'{}' THEN
   IF jsonb_typeof(input)<>'object' OR (input-'action'-'revision'-'enabled'-'silent'-'quiet_hours')<>'{}'
     OR input->>'action' IS DISTINCT FROM 'save'
     OR jsonb_typeof(input->'revision') IS DISTINCT FROM 'number'
     OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(pref.revision,0))
     OR jsonb_typeof(input->'enabled') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'Settings changed or invalid; reload settings'; END IF;
   FOR k,v IN SELECT * FROM jsonb_each(input->'enabled') LOOP
    IF NOT k=ANY(public.telegram_route_keys()) OR jsonb_typeof(v)<>'boolean' THEN RAISE EXCEPTION 'Invalid alert category'; END IF;
   END LOOP;
   sound:=coalesce(input->'silent',pref.silent,'{}');
   quiet:=coalesce(input->'quiet_hours',pref.quiet_hours,'{"enabled":false,"start":"22:00","end":"07:00","timezone":"UTC"}');
   IF jsonb_typeof(sound) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'Invalid silent preferences'; END IF;
   FOR k,v IN SELECT * FROM jsonb_each(sound) LOOP
    IF NOT k=ANY(public.telegram_route_keys()) OR jsonb_typeof(v)<>'boolean' THEN RAISE EXCEPTION 'Invalid silent category'; END IF;
   END LOOP;
   IF jsonb_typeof(quiet) IS DISTINCT FROM 'object'
     OR (quiet-'enabled'-'start'-'end'-'timezone')<>'{}'
     OR jsonb_typeof(quiet->'enabled') IS DISTINCT FROM 'boolean'
     OR jsonb_typeof(quiet->'start') IS DISTINCT FROM 'string'
     OR jsonb_typeof(quiet->'end') IS DISTINCT FROM 'string'
     OR jsonb_typeof(quiet->'timezone') IS DISTINCT FROM 'string'
     OR NOT coalesce(quiet->>'start' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$',false)
     OR NOT coalesce(quiet->>'end' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$',false)
     OR NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=quiet->>'timezone')
     OR (quiet->>'enabled'='true' AND quiet->>'start'=quiet->>'end')
   THEN RAISE EXCEPTION 'Choose valid quiet hours, distinct start/end times and a valid timezone'; END IF;
   INSERT INTO public.telegram_alert_preferences AS p (installation,user_id,revision,enabled,silent,quiet_hours)
    VALUES(installation,auth.uid(),1,input->'enabled',sound,quiet)
    ON CONFLICT ON CONSTRAINT telegram_alert_preferences_pkey DO UPDATE SET enabled=excluded.enabled,silent=excluded.silent,quiet_hours=excluded.quiet_hours,revision=p.revision+1 RETURNING * INTO pref;
  END IF;
  SELECT r.routes INTO reg FROM public.telegram_recipients r WHERE r.installation=installation AND r.user_id=auth.uid();
  RETURN jsonb_build_object('revision',coalesce(pref.revision,0),'enabled',defaults||coalesce(pref.enabled,'{}'),
    'silent',coalesce(pref.silent,'{}'),'quiet_hours',coalesce(pref.quiet_hours,'{"enabled":false,"start":"22:00","end":"07:00","timezone":"UTC"}'::jsonb),
    'registered',reg IS NOT NULL,'groups',coalesce(reg,'{}'),'editable',true);
 END IF;
 RETURN public.dashboard_api_before_notification_sound(installation,operation,input);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_notification_sound(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;

CREATE OR REPLACE FUNCTION bot_runtime.personal_recipients() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('user_id',r.user_id,'telegram_user_id',r.telegram_user_id,
  'routes',r.routes,'enabled',coalesce(p.enabled,'{}'),'silent',coalesce(p.silent,'{}'),'quiet_hours',p.quiet_hours,'created_at',extract(epoch FROM r.created_at))),'[]')
 FROM public.telegram_recipients r JOIN bot_runtime.runtime_control c ON c.installation=r.installation AND c.enabled
 LEFT JOIN public.telegram_alert_preferences p ON p.installation=r.installation AND p.user_id=r.user_id;
$$;
COMMIT;
