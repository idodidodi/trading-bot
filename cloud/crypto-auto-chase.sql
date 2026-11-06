-- Apply after divergence-chasing.sql and kraken-intraday.sql. Owner-only opt-in.
BEGIN;
CREATE TABLE IF NOT EXISTS public.crypto_auto_chase_preferences (
 installation uuid NOT NULL REFERENCES public.dashboard_installations(id),
 user_id uuid NOT NULL REFERENCES auth.users(id), enabled boolean NOT NULL DEFAULT false,
 revision bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(installation,user_id));
ALTER TABLE public.crypto_auto_chase_preferences ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.crypto_auto_chase_preferences FROM PUBLIC,anon,authenticated;
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_crypto_auto_chase(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_crypto_auto_chase;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
#variable_conflict use_variable
DECLARE pref public.crypto_auto_chase_preferences; routes jsonb; chat text;
BEGIN
 IF operation='/api/crypto-auto-chase-settings' THEN
  IF auth.uid() IS NULL OR NOT EXISTS(SELECT 1 FROM dashboard_installations i WHERE i.id=installation AND i.owner_id=auth.uid()) THEN
   RAISE EXCEPTION 'Installation owner required';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(installation::text||':crypto-auto-chase',0));
  SELECT * INTO pref FROM crypto_auto_chase_preferences p WHERE p.installation=installation AND p.user_id=auth.uid();
  SELECT r.routes INTO routes FROM telegram_recipients r WHERE r.installation=installation AND r.user_id=auth.uid();
  IF input<>'{}' THEN
   IF (input-'action'-'revision'-'enabled'-'chat')<>'{}' OR input->>'action' IS DISTINCT FROM 'save'
    OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(pref.revision,0))
    OR jsonb_typeof(input->'enabled') IS DISTINCT FROM 'boolean'
    OR jsonb_typeof(input->'chat') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'Invalid settings or stale revision'; END IF;
   chat:=input->>'chat';
   IF routes IS NULL THEN RAISE EXCEPTION 'Verify and enroll Telegram recipient first'; END IF;
   IF chat<>'' AND chat !~ '^-[1-9][0-9]{0,18}$' THEN RAISE EXCEPTION 'A numeric group chat ID is required'; END IF;
   IF (input->>'enabled')::boolean AND chat='' THEN RAISE EXCEPTION 'Register the automatic crypto group before enabling'; END IF;
   IF chat<>'' THEN
    PERFORM chat::bigint;
    IF EXISTS(SELECT 1 FROM telegram_recipients r CROSS JOIN LATERAL jsonb_each_text(r.routes) e
      WHERE r.installation=installation AND e.value=chat AND (r.user_id<>auth.uid() OR e.key<>'crypto_auto_chase'))
      OR EXISTS(SELECT 1 FROM public.telegram_routes r CROSS JOIN LATERAL jsonb_each_text(r.routes) e
       WHERE r.installation=installation AND e.value=chat) THEN RAISE EXCEPTION 'Chat is already assigned to another route'; END IF;
   END IF;
   UPDATE telegram_recipients r SET routes=r.routes||jsonb_build_object('crypto_auto_chase',chat)
    WHERE r.installation=installation AND r.user_id=auth.uid();
   INSERT INTO crypto_auto_chase_preferences AS p(installation,user_id,enabled)
    VALUES(installation,auth.uid(),(input->>'enabled')::boolean)
    ON CONFLICT ON CONSTRAINT crypto_auto_chase_preferences_pkey DO UPDATE SET enabled=excluded.enabled,
     revision=p.revision+1,created_at=CASE WHEN excluded.enabled AND NOT p.enabled THEN now() ELSE p.created_at END
    RETURNING * INTO pref;
   routes:=routes||jsonb_build_object('crypto_auto_chase',chat);
  END IF;
  RETURN jsonb_build_object('revision',coalesce(pref.revision,0),'enabled',coalesce(pref.enabled,false),
    'chat',coalesce(routes->>'crypto_auto_chase',''));
 END IF;
 RETURN public.dashboard_api_before_crypto_auto_chase(installation,operation,input);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_crypto_auto_chase(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;
CREATE OR REPLACE FUNCTION bot_runtime.chasing_recipients() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('user_id',r.user_id,'telegram_user_id',r.telegram_user_id,
 'routes',r.routes,'stop_pct',coalesce(p.stop_pct,.5),'revision',coalesce(p.revision,0),
 'created_at',extract(epoch FROM coalesce(p.created_at,a.created_at)),
 'chasing',coalesce(p.chasing,false),'live_positions',coalesce(p.live_positions,false),
 'crypto_auto_chase',coalesce(a.enabled,false),'auto_created_at',extract(epoch FROM a.created_at))), '[]')
 FROM public.telegram_recipients r LEFT JOIN public.chasing_preferences p USING(installation,user_id)
 LEFT JOIN public.crypto_auto_chase_preferences a USING(installation,user_id)
 JOIN public.dashboard_installations i ON i.id=r.installation AND i.owner_id=r.user_id
 JOIN bot_runtime.runtime_control c ON c.installation=r.installation AND c.enabled
 WHERE p.installation IS NOT NULL OR a.installation IS NOT NULL;
$$;
REVOKE ALL ON FUNCTION bot_runtime.chasing_recipients() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION bot_runtime.chasing_recipients() TO trading_worker;
COMMIT;
