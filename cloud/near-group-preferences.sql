-- Apply after telegram-asset-subscriptions.sql; the existing destination stays authoritative.
BEGIN;
CREATE TABLE IF NOT EXISTS bot_runtime.near_group_preferences (
 chat text NOT NULL,
 asset text NOT NULL DEFAULT 'NEARUSD' CHECK(asset='NEARUSD'),
 revision bigint NOT NULL DEFAULT 0,
 subscriptions jsonb NOT NULL,
 enrolled jsonb NOT NULL DEFAULT '{}',
 PRIMARY KEY(chat,asset),
 FOREIGN KEY(chat,asset) REFERENCES bot_runtime.telegram_asset_subscriptions(chat,asset)
   ON UPDATE CASCADE ON DELETE CASCADE
);
REVOKE ALL ON bot_runtime.near_group_preferences FROM PUBLIC,anon,authenticated,trading_worker;
GRANT SELECT ON bot_runtime.near_group_preferences TO trading_worker;

DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_near_preferences(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_near_preferences;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_variable
DECLARE sub bot_runtime.telegram_asset_subscriptions; pref bot_runtime.near_group_preferences;
 frames text[]:=ARRAY['5m','15m','30m','1h','4h','daily','weekly'];
 selections jsonb; starts jsonb; strategy text; frame text; picked jsonb; old_frames jsonb;
 can_edit boolean; n integer; selected_rsi text[];
BEGIN
 IF split_part(operation,'?',1)<>'/api/near-alerts' THEN
  RETURN public.dashboard_api_before_near_preferences(installation,operation,input);
 END IF;
 IF auth.uid() IS NULL OR NOT public.dashboard_can_view(installation) THEN
  RAISE EXCEPTION 'Not authorized' USING ERRCODE='42501';
 END IF;
 SELECT EXISTS(SELECT 1 FROM public.dashboard_installations i
   WHERE i.id=installation AND i.owner_id=auth.uid()) INTO can_edit;
 -- Shared group settings belong to the installation owner. Never accept a chat ID from a caller.
 IF NOT can_edit THEN RAISE EXCEPTION 'Owner only' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.id=1 AND c.installation=installation) THEN
  RAISE EXCEPTION 'Not authorized' USING ERRCODE='42501';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('near-alerts:'||installation::text,0));
 SELECT count(*) INTO n FROM bot_runtime.telegram_asset_subscriptions s WHERE s.asset='NEARUSD';
 IF n<>1 THEN RAISE EXCEPTION 'A single NEAR group must be configured by the operator'; END IF;
 SELECT * INTO sub FROM bot_runtime.telegram_asset_subscriptions s WHERE s.asset='NEARUSD' FOR UPDATE;
 SELECT * INTO pref FROM bot_runtime.near_group_preferences p WHERE p.chat=sub.chat AND p.asset=sub.asset;
 selections:=coalesce(pref.subscriptions,jsonb_build_object('rsi',CASE WHEN sub.enabled THEN to_jsonb(sub.timeframes) ELSE '[]'::jsonb END,'trend-pin-bars','[]'::jsonb));
 starts:=coalesce(pref.enrolled,'{}');
 IF input<>'{}'::jsonb THEN
  IF jsonb_typeof(input)<>'object' OR (input-'action'-'revision'-'subscriptions')<>'{}'
    OR input->>'action' IS DISTINCT FROM 'save'
    OR input->'revision' IS DISTINCT FROM to_jsonb(coalesce(pref.revision,0))
    OR jsonb_typeof(input->'subscriptions') IS DISTINCT FROM 'object'
    OR ((input->'subscriptions')-'rsi'-'trend-pin-bars')<>'{}'
    OR NOT (input->'subscriptions' ?& ARRAY['rsi','trend-pin-bars']) THEN
   RAISE EXCEPTION 'Settings changed or invalid; reload settings';
  END IF;
  FOREACH strategy IN ARRAY ARRAY['rsi','trend-pin-bars'] LOOP
   picked:=input->'subscriptions'->strategy;
   IF jsonb_typeof(picked) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Select valid timeframes'; END IF;
   IF jsonb_array_length(picked)>7 OR EXISTS(SELECT 1 FROM jsonb_array_elements(picked) f
     WHERE jsonb_typeof(f)<>'string' OR NOT (f#>>'{}')=ANY(frames)) THEN RAISE EXCEPTION 'Select valid timeframes'; END IF;
   IF (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(picked))<>jsonb_array_length(picked) THEN
    RAISE EXCEPTION 'Duplicate timeframe';
   END IF;
   old_frames:=selections->strategy;
   FOR frame IN SELECT jsonb_array_elements_text(picked) LOOP
    starts:=jsonb_set(starts,ARRAY[strategy||':'||frame],
      CASE WHEN old_frames ? frame THEN coalesce(starts->(strategy||':'||frame),to_jsonb(extract(epoch FROM sub.created_at)))
           ELSE to_jsonb(extract(epoch FROM clock_timestamp())) END,true);
   END LOOP;
  END LOOP;
  selections:=input->'subscriptions';
  INSERT INTO bot_runtime.near_group_preferences AS p(chat,asset,revision,subscriptions,enrolled)
   VALUES(sub.chat,sub.asset,coalesce(pref.revision,0)+1,selections,starts)
   ON CONFLICT(chat,asset) DO UPDATE SET revision=excluded.revision,subscriptions=excluded.subscriptions,enrolled=excluded.enrolled
   RETURNING * INTO pref;
  SELECT coalesce(array_agg(value),'{}'::text[]) INTO selected_rsi FROM jsonb_array_elements_text(selections->'rsi');
  UPDATE bot_runtime.telegram_asset_subscriptions s SET enabled=cardinality(selected_rsi)>0,
   timeframes=CASE WHEN cardinality(selected_rsi)>0 THEN selected_rsi ELSE s.timeframes END
   WHERE s.chat=sub.chat AND s.asset=sub.asset;
 END IF;
 RETURN jsonb_build_object('revision',coalesce(pref.revision,0),'editable',can_edit,'asset','NEARUSD',
   'timeframes',to_jsonb(frames),'subscriptions',selections);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_near_preferences(uuid,text,jsonb),public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;
COMMIT;
