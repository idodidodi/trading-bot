-- Apply after telegram-notification-sound.sql, before publishing the UI/worker.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.telegram_group_sound(
 installation uuid PRIMARY KEY REFERENCES public.dashboard_installations(id) ON DELETE CASCADE,
 revision bigint NOT NULL DEFAULT 0, silent jsonb NOT NULL DEFAULT '{}');
REVOKE ALL ON bot_runtime.telegram_group_sound FROM PUBLIC,anon,authenticated,trading_worker;
GRANT SELECT ON bot_runtime.telegram_group_sound TO trading_worker;
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_group_sound(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_group_sound;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_variable
DECLARE pref bot_runtime.telegram_group_sound; group_key text; frames jsonb; frame text; setting jsonb;
 groups jsonb:='{"near":"NEAR","higher":"RSI Higher TF Reached","lower":"RSI Lower TF Reached","ready":"Stock RSI Setup Ready","invalid":"RSI Stock Setup Invalidated"}';
BEGIN
 IF split_part(operation,'?',1)<>'/api/telegram-group-sound' THEN
  RETURN public.dashboard_api_before_group_sound(installation,operation,input);
 END IF;
 IF auth.uid() IS NULL OR NOT public.dashboard_can_view(installation) THEN RAISE EXCEPTION 'Not authorized'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.dashboard_installations i WHERE i.id=installation AND i.owner_id=auth.uid()) THEN
  IF input<>'{}' THEN RAISE EXCEPTION 'Owner only'; END IF;
  RETURN jsonb_build_object('editable',false,'groups','{}'::jsonb);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=installation) THEN RAISE EXCEPTION 'Not authorized'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('telegram-group-sound:'||installation::text,0));
 SELECT * INTO pref FROM bot_runtime.telegram_group_sound p WHERE p.installation=installation;
 IF input<>'{}' THEN
  IF jsonb_typeof(input) IS DISTINCT FROM 'object' OR (input-'action'-'revision'-'silent')<>'{}'
    OR input->>'action' IS DISTINCT FROM 'save'
    OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(pref.revision,0))
    OR jsonb_typeof(input->'silent') IS DISTINCT FROM 'object'
  THEN RAISE EXCEPTION 'Settings changed or invalid; reload settings'; END IF;
  FOR group_key,frames IN SELECT * FROM jsonb_each(input->'silent') LOOP
   IF NOT groups ? group_key OR jsonb_typeof(frames) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'Invalid group'; END IF;
   FOR frame,setting IN SELECT * FROM jsonb_each(frames) LOOP
    IF NOT frame=ANY(ARRAY['5m','15m','30m','1h','4h','daily','weekly','monthly']) OR jsonb_typeof(setting)<>'boolean' THEN RAISE EXCEPTION 'Invalid silent timeframe'; END IF;
   END LOOP;
  END LOOP;
  INSERT INTO bot_runtime.telegram_group_sound AS p VALUES(installation,1,input->'silent')
   ON CONFLICT ON CONSTRAINT telegram_group_sound_pkey DO UPDATE SET revision=p.revision+1,silent=excluded.silent RETURNING * INTO pref;
 END IF;
 RETURN jsonb_build_object('editable',true,'groups',groups,'revision',coalesce(pref.revision,0),'silent',coalesce(pref.silent,'{}'));
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_group_sound(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;
COMMIT;
