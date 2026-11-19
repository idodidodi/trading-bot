-- Local runtime only. Keep replay work on the existing single scanner worker.
BEGIN;
DO $$DECLARE definition text;BEGIN
 definition:=pg_get_functiondef('bot_runtime.api(uuid,text,jsonb)'::regprocedure);
 IF position('"executor":"local"' IN definition)=0 THEN
  IF position('"executor":"edge"' IN definition)=0 THEN RAISE EXCEPTION 'Unexpected replay dispatcher'; END IF;
  definition:=replace(definition,'''running'',(input||''{"source":"csv","executor":"edge"}'')', '''pending'',(input||''{"source":"csv","executor":"local"}'')');
  definition:=replace(definition,'''status'',''running'',''runtime'',true,''executor'',''edge''', '''status'',''pending'',''runtime'',true,''executor'',''local''');
  IF position('"executor":"local"' IN definition)=0 THEN RAISE EXCEPTION 'Replay dispatcher was not converted'; END IF;
  EXECUTE definition;
 END IF;
END $$;

-- Only the trusted gateway can save its computed report. Recheck membership
-- and the administrator identity mapping after computation, before saving.
CREATE OR REPLACE FUNCTION api_private.save_csv(issuer text,subject text,inst uuid,report jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid uuid; authorized jsonb;
BEGIN
 SELECT c.user_id INTO uid FROM api_private.clerk_identities c
 WHERE c.issuer=save_csv.issuer AND c.subject=save_csv.subject AND c.enabled;
 IF uid IS NULL THEN RAISE EXCEPTION 'Not authorized' USING ERRCODE='42501'; END IF;
 PERFORM set_config('request.jwt.claim.sub',uid::text,true);
 PERFORM set_config('request.jwt.claims',jsonb_build_object('sub',uid,'role','authenticated')::text,true);
 authorized:=public.dashboard_api(inst,'/api/csv-backtests','{"action":"authorize"}'::jsonb);
 IF authorized->>'authorized' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'Not authorized' USING ERRCODE='42501'; END IF;
 PERFORM public.dashboard_save_csv_backtest(inst,report);
 RETURN public.dashboard_api(inst,'/api/csv-backtests?id='||(report->>'id'),'{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION api_private.save_csv(text,text,uuid,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION api_private.save_csv(text,text,uuid,jsonb) TO dashboard_gateway;
COMMIT;
