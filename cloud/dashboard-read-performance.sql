-- Apply after postgres-dashboard.sql and subsequent runtime migrations.
-- Read-only dashboard optimization; no scanner or alert behavior changes.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';
CREATE OR REPLACE FUNCTION bot_runtime.backtest_page(operation text) RETURNS jsonb
LANGUAGE plpgsql SET search_path=bot_runtime,public,pg_catalog,pg_temp AS $$
DECLARE
 q text:=split_part(operation,'?',2);
 page bigint:=coalesce(substring(q FROM 'page=([^&]+)')::bigint,0);
 sort_field text:=coalesce(substring(q FROM 'sort=([^&]+)'),'confirmed_at');
 sort_order text:=coalesce(substring(q FROM 'order=([^&]+)'),'asc');
 has_run boolean;report_text text;report jsonb;items jsonb;summary jsonb;total_count bigint;
BEGIN
 IF page<0 OR page>10000 THEN RAISE EXCEPTION 'Invalid page';END IF;
 IF sort_field NOT IN ('asset','timeframe','evidence','confirmed_at','rating') OR sort_order NOT IN ('asc','desc') THEN RAISE EXCEPTION 'Invalid sort';END IF;
 -- Read and expand only the latest run, once. The former runtime_records
 -- projection hashed/sorted all live signals and historical runs repeatedly.
 SELECT b.report INTO report_text FROM backtest_runs b ORDER BY id DESC LIMIT 1;
 has_run:=FOUND;report:=report_text::jsonb;
 SELECT coalesce(sum(jsonb_array_length(result->'signals')),0),
 coalesce(jsonb_agg((result-'signals'-'followups')||jsonb_build_object('signals',jsonb_array_length(result->'signals'))),'[]')
 INTO total_count,summary FROM jsonb_array_elements(report->'results') r(result);
 WITH current_rows AS MATERIALIZED (
  SELECT DISTINCT ON(k.finding_id) k.finding_id id,
   jsonb_build_object('id',k.finding_id,'signal',s.signal,'status','saved','attempts',0,'error',null) payload
  FROM jsonb_array_elements(report->'results') r(result)
  CROSS JOIN LATERAL jsonb_array_elements(r.result->'signals') s(signal)
  JOIN finding_keys k ON k.source='backtest' AND k.evidence_hash=sha256(convert_to(s.signal::text,'UTF8'))
  ORDER BY k.finding_id
 ), rows AS (
  SELECT * FROM current_rows
  UNION ALL
  SELECT id,payload::jsonb FROM legacy_findings WHERE source='backtest' AND NOT has_run
 ), selected AS (
  SELECT r.id,r.payload,f.finding_id feedback_id,f.source,f.evidence,f.rating,f.comment,f.revision,f.updated_at,
   coalesce(s.status,'active')='archived' archived,coalesce(s.revision,0) state_revision
  FROM rows r LEFT JOIN finding_feedback f ON f.finding_id=r.id
  LEFT JOIN finding_state s ON s.finding_id=r.id
  WHERE coalesce(s.status,'active')<>'deleted'
   AND (coalesce(substring(q FROM 'archived=([^&]+)'),'false')='true' OR coalesce(s.status,'active')<>'archived')
  ORDER BY CASE WHEN sort_order='asc' THEN public.dashboard_backtest_sort_value(r.payload->'signal',CASE WHEN f.finding_id IS NOT NULL THEN jsonb_build_object('rating',f.rating) END,sort_field) END ASC NULLS LAST,
   CASE WHEN sort_order='desc' THEN public.dashboard_backtest_sort_value(r.payload->'signal',CASE WHEN f.finding_id IS NOT NULL THEN jsonb_build_object('rating',f.rating) END,sort_field) END DESC NULLS LAST,
   (r.payload->'signal'->>'confirmed_at')::bigint DESC,r.id
  LIMIT 51 OFFSET page*50
 )
 SELECT coalesce(jsonb_agg(r.payload||jsonb_build_object(
  'feedback',CASE WHEN r.feedback_id IS NOT NULL THEN jsonb_build_object('finding_id',r.feedback_id,'source',r.source,'evidence',r.evidence::jsonb,'rating',r.rating,'comment',r.comment,'revision',r.revision,'updated_at',r.updated_at) END,
  'archived',r.archived,'state_revision',r.state_revision,'followup',u.results::jsonb->'recovery') ORDER BY
  CASE WHEN sort_order='asc' THEN public.dashboard_backtest_sort_value(r.payload->'signal',CASE WHEN r.feedback_id IS NOT NULL THEN jsonb_build_object('rating',r.rating) END,sort_field) END ASC NULLS LAST,
  CASE WHEN sort_order='desc' THEN public.dashboard_backtest_sort_value(r.payload->'signal',CASE WHEN r.feedback_id IS NOT NULL THEN jsonb_build_object('rating',r.rating) END,sort_field) END DESC NULLS LAST,
  (r.payload->'signal'->>'confirmed_at')::bigint DESC,r.id),'[]') INTO items
 FROM selected r LEFT JOIN finding_followup u ON u.finding_id=r.id;
 RETURN jsonb_build_object('total_count',total_count,'page_count',(total_count+49)/50,
  'findings',CASE WHEN jsonb_array_length(items)>50 THEN items-50 ELSE items END,'has_more',jsonb_array_length(items)>50,
  'summary',summary,'scan',(SELECT payload::jsonb FROM scanner_status WHERE id=1));
END $$;
REVOKE ALL ON FUNCTION bot_runtime.backtest_page(text) FROM PUBLIC,anon,authenticated;

-- Keep the current dispatcher and all its authorization checks. Fail closed if
-- the expected anchors have changed; safe to reapply this migration.
DO $$
DECLARE definition text;
BEGIN
 definition:=pg_get_functiondef('bot_runtime.api(uuid,text,jsonb)'::regprocedure);
 IF position('bot_runtime.backtest_page(operation)' IN definition)=0 THEN
  IF position($a$ELSIF path='/api/findings' THEN$a$ IN definition)=0 THEN RAISE EXCEPTION 'Findings dispatcher anchor missing';END IF;
  definition:=replace(definition,$a$ELSIF path='/api/findings' THEN$a$,$b$ELSIF path='/api/findings' THEN
  IF source='backtest' THEN RETURN bot_runtime.backtest_page(operation);END IF;$b$);
 END IF;
 IF position($a$path='/api/overview'$a$ IN definition)=0 THEN
  IF position($a$IF path='/api/assets' THEN$a$ IN definition)=0 THEN RAISE EXCEPTION 'Assets dispatcher anchor missing';END IF;
  definition:=replace(definition,$a$IF path='/api/assets' THEN$a$,$b$IF path='/api/overview' THEN
  RETURN jsonb_build_object('summary',coalesce((SELECT payload::jsonb->'coverage' FROM scanner_status WHERE id=1),'[]'),
   'scan',(SELECT payload::jsonb FROM scanner_status WHERE id=1));
 ELSIF path='/api/assets' THEN$b$);
 END IF;
 EXECUTE definition;
END $$;
COMMIT;
