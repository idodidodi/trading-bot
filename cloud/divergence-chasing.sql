-- Additive, opt-in. Apply after telegram-personal.sql. No destinations are provisioned.
BEGIN;
CREATE TABLE IF NOT EXISTS public.chasing_preferences (
 installation uuid NOT NULL REFERENCES public.dashboard_installations(id),
 user_id uuid NOT NULL REFERENCES auth.users(id), revision bigint NOT NULL DEFAULT 1,
 stop_pct numeric NOT NULL DEFAULT .5 CHECK(stop_pct>0 AND stop_pct<100),
 created_at timestamptz NOT NULL DEFAULT now(),
 chasing boolean NOT NULL DEFAULT false, live_positions boolean NOT NULL DEFAULT false,
 PRIMARY KEY(installation,user_id));
ALTER TABLE public.chasing_preferences ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chasing_preferences FROM PUBLIC,anon,authenticated;
CREATE TABLE IF NOT EXISTS bot_runtime.chasing_sessions (
 id text PRIMARY KEY, installation uuid NOT NULL REFERENCES public.dashboard_installations(id),
 user_id uuid NOT NULL, state jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS bot_runtime.chasing_outbox (
 id text PRIMARY KEY, session_id text NOT NULL REFERENCES bot_runtime.chasing_sessions(id),
 event jsonb NOT NULL, status text NOT NULL DEFAULT 'pending', message_id bigint,
 chat text, telegram_user_id bigint, bot text, dismissal_id text,
 retry_at bigint NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS bot_runtime.chasing_price_slots(
 user_id uuid PRIMARY KEY, event_id text NOT NULL, reserved_at double precision NOT NULL);
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.chasing_price_slots TO trading_worker;
REVOKE ALL ON bot_runtime.chasing_price_slots FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON bot_runtime.chasing_sessions,bot_runtime.chasing_outbox TO trading_worker;
REVOKE ALL ON bot_runtime.chasing_sessions,bot_runtime.chasing_outbox FROM PUBLIC,anon,authenticated;
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_chasing(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_chasing;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
#variable_conflict use_variable
DECLARE pref public.chasing_preferences; reg jsonb; target uuid; pct numeric;
BEGIN
 IF operation IN ('/api/chasing-settings','/api/chasing-registration') THEN
  IF auth.uid() IS NULL OR NOT public.dashboard_can_view(installation) THEN RAISE EXCEPTION 'Not authorized'; END IF;
  IF operation='/api/chasing-registration' THEN
   IF NOT EXISTS(SELECT 1 FROM dashboard_installations i WHERE i.id=installation AND i.owner_id=auth.uid()) THEN RAISE EXCEPTION 'Owner required';END IF;
   PERFORM pg_advisory_xact_lock(hashtextextended(installation::text||':chasing-registration',0));
   IF input='{}' THEN
    RETURN jsonb_build_object('members',(SELECT jsonb_agg(jsonb_build_object('user_id',u.id,
     'label',coalesce(to_jsonb(u)->>'email',u.id::text),'routes',coalesce(r.routes,'{}')))
     FROM auth.users u LEFT JOIN telegram_recipients r ON r.user_id=u.id AND r.installation=installation
     WHERE u.id IN (SELECT i.owner_id FROM dashboard_installations i WHERE i.id=installation
       UNION SELECT m.user_id FROM dashboard_members m WHERE m.installation=installation)));
   END IF;
   target:=(input->>'user_id')::uuid;
   IF NOT EXISTS(SELECT 1 FROM dashboard_installations i WHERE i.id=installation AND i.owner_id=target)
      AND NOT EXISTS(SELECT 1 FROM dashboard_members m WHERE m.installation=installation AND m.user_id=target) THEN RAISE EXCEPTION 'Member required';END IF;
   IF input->>'chasing' !~ '^-[1-9][0-9]{0,18}$' OR input->>'live_positions' !~ '^-[1-9][0-9]{0,18}$'
      OR input->>'chasing'=input->>'live_positions' OR input->>'chasing' IS NULL OR input->>'live_positions' IS NULL THEN RAISE EXCEPTION 'Two distinct group IDs required'; END IF;
   PERFORM (input->>'chasing')::bigint,(input->>'live_positions')::bigint;
   IF EXISTS(SELECT 1 FROM telegram_recipients r CROSS JOIN LATERAL jsonb_each_text(r.routes) e
    WHERE r.installation=installation AND e.value IN (input->>'chasing',input->>'live_positions')
    AND (r.user_id<>target OR e.key NOT IN ('chasing','live_positions'))) THEN RAISE EXCEPTION 'Chat is already assigned to another route or recipient';END IF;
   UPDATE telegram_recipients r SET routes=r.routes||jsonb_build_object('chasing',input->>'chasing','live_positions',input->>'live_positions')
    WHERE r.installation=installation AND r.user_id=target;
   IF NOT FOUND THEN RAISE EXCEPTION 'Verify and enroll Telegram recipient first'; END IF;
   RETURN jsonb_build_object('saved',true);
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(installation::text||auth.uid()::text||'chasing',0));
  SELECT * INTO pref FROM chasing_preferences p WHERE p.installation=installation AND p.user_id=auth.uid();
  IF input<>'{}' THEN
   IF (input-'action'-'revision'-'stop_pct'-'chasing'-'live_positions')<>'{}'
    OR input->>'action' IS DISTINCT FROM 'save' OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(pref.revision,0))
    OR jsonb_typeof(input->'stop_pct') IS DISTINCT FROM 'number'
    OR jsonb_typeof(input->'chasing') IS DISTINCT FROM 'boolean'
    OR jsonb_typeof(input->'live_positions') IS DISTINCT FROM 'boolean' THEN RAISE EXCEPTION 'Invalid settings or stale revision';END IF;
   pct:=(input->>'stop_pct')::numeric;
   IF NOT pct>0 OR NOT pct<100 THEN RAISE EXCEPTION 'Percentage must be between zero and 100';END IF;
   INSERT INTO chasing_preferences AS p(installation,user_id,revision,stop_pct,chasing,live_positions)
    VALUES(installation,auth.uid(),1,pct,(input->>'chasing')::boolean,(input->>'live_positions')::boolean)
    ON CONFLICT ON CONSTRAINT chasing_preferences_pkey DO UPDATE SET revision=p.revision+1,stop_pct=excluded.stop_pct,chasing=excluded.chasing,live_positions=excluded.live_positions RETURNING * INTO pref;
  END IF;
  SELECT r.routes INTO reg FROM telegram_recipients r WHERE r.installation=installation AND r.user_id=auth.uid();
  RETURN jsonb_build_object('revision',coalesce(pref.revision,0),'stop_pct',coalesce(pref.stop_pct,.5),
   'chasing',coalesce(pref.chasing,false),'live_positions',coalesce(pref.live_positions,false),
   'groups',coalesce(reg,'{}'), 'sessions',coalesce((SELECT jsonb_agg(s.state) FROM
    (SELECT state FROM bot_runtime.chasing_sessions WHERE user_id=auth.uid() AND chasing_sessions.installation=installation
      ORDER BY state->>'started_at' DESC LIMIT 50) s),'[]'));
 END IF;
 RETURN public.dashboard_api_before_chasing(installation,operation,input);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_chasing(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;
CREATE OR REPLACE FUNCTION bot_runtime.chasing_recipients() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('user_id',r.user_id,'telegram_user_id',r.telegram_user_id,
 'routes',r.routes,'stop_pct',p.stop_pct,'revision',p.revision,'created_at',extract(epoch FROM p.created_at),'chasing',p.chasing,'live_positions',p.live_positions)), '[]')
 FROM public.telegram_recipients r JOIN public.chasing_preferences p USING(installation,user_id)
 JOIN bot_runtime.runtime_control c ON c.installation=r.installation AND c.enabled;
$$;
REVOKE ALL ON FUNCTION bot_runtime.chasing_recipients() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION bot_runtime.chasing_recipients() TO trading_worker;
COMMIT;
