-- Apply after the current runtime migrations, including ma-cross.sql.
-- UI reads only: scanner qualification, routing and delivery are unchanged.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='60s';
-- Encoding is fixed explicitly, making this canonical JSON hash deterministic.
CREATE OR REPLACE FUNCTION bot_runtime.signal_payload_hash(value text) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
 SELECT sha256(convert_to(value::jsonb::text,'UTF8'));
$$;
REVOKE ALL ON FUNCTION bot_runtime.signal_payload_hash(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bot_runtime.signal_payload_hash(text) TO trading_worker;

-- Unlike runtime_findings, this view never unions/sorts backtest history.
CREATE OR REPLACE VIEW bot_runtime.signal_search_live AS
 SELECT 'rsi'::text strategy,k.finding_id id,
 coalesce((s.payload::jsonb->>'confirmed_at')::bigint,-1) confirmed_at,
 s.payload::jsonb->>'symbol' symbol,s.payload::jsonb->>'timeframe' timeframe,s.payload::jsonb->>'direction' direction,
 jsonb_build_object('id',k.finding_id,'signal',s.payload::jsonb,'status',s.status,'attempts',s.attempts,'error',s.error) payload
 FROM bot_runtime.signals s JOIN LATERAL (SELECT finding_id FROM bot_runtime.finding_keys
 WHERE source='live' AND evidence_hash=bot_runtime.signal_payload_hash(s.payload) OFFSET 0) k ON true
;
CREATE OR REPLACE VIEW bot_runtime.signal_search_legacy(strategy,id,confirmed_at,symbol,timeframe,direction,payload) AS
 SELECT 'rsi'::text strategy,l.id,coalesce((l.payload::jsonb->'signal'->>'confirmed_at')::bigint,-1),
 l.payload::jsonb->'signal'->>'symbol',l.payload::jsonb->'signal'->>'timeframe',l.payload::jsonb->'signal'->>'direction',l.payload::jsonb
 FROM bot_runtime.legacy_findings l WHERE l.source='live' AND NOT EXISTS(
 SELECT 1 FROM bot_runtime.finding_keys k JOIN bot_runtime.signals s
 ON bot_runtime.signal_payload_hash(s.payload)=k.evidence_hash WHERE k.source='live' AND k.finding_id=l.id OFFSET 0)
;
CREATE OR REPLACE VIEW bot_runtime.signal_search_rows AS
 SELECT * FROM bot_runtime.signal_search_live
 UNION ALL SELECT * FROM bot_runtime.signal_search_legacy
 UNION ALL SELECT 'pin-bars',id,coalesce((payload::jsonb->>'confirmed_at')::bigint,-1),payload::jsonb->>'symbol',payload::jsonb->>'timeframe',payload::jsonb->>'direction',payload::jsonb FROM bot_runtime.pin_bar_signals
 UNION ALL SELECT 'bb-pin-bars',id,coalesce((payload::jsonb->>'confirmed_at')::bigint,-1),payload::jsonb->>'symbol',payload::jsonb->>'timeframe',payload::jsonb->>'direction',payload::jsonb FROM bot_runtime.bb_pin_bar_signals
 UNION ALL SELECT 'rsi-pb',id,coalesce((payload::jsonb->>'confirmed_at')::bigint,-1),payload::jsonb->>'symbol',payload::jsonb->>'timeframe',payload::jsonb->>'direction',payload::jsonb FROM bot_runtime.rsi_pb_signals
 UNION ALL SELECT 'ma-cross',id,coalesce((payload::jsonb->>'confirmed_at')::bigint,-1),payload::jsonb->>'symbol',payload::jsonb->>'timeframe',payload::jsonb->>'direction',payload::jsonb FROM bot_runtime.ma_cross_signals
;
REVOKE ALL ON bot_runtime.signal_search_rows,bot_runtime.signal_search_live,bot_runtime.signal_search_legacy FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION public.dashboard_signal_query(inst uuid,filters jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,bot_runtime AS $$
DECLARE
 strategy text:=coalesce(filters->>'strategy','rsi');
 predicate text;page_query text;source_query text;items jsonb;result jsonb;scan jsonb;catalog boolean;archived boolean;
 field text;value text;bound timestamp;cursor_time bigint;cursor_id text;last_row jsonb;
BEGIN
 IF NOT public.dashboard_can_view(inst) OR NOT EXISTS(SELECT 1 FROM bot_runtime.runtime_control WHERE installation=inst AND enabled) THEN
  RAISE EXCEPTION 'Not authorized' USING ERRCODE='42501';
 END IF;
 IF jsonb_typeof(filters) IS DISTINCT FROM 'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(filters) k
 WHERE k NOT IN ('strategy','asset','query','timeframe','direction','from','to','archived','catalog','cursor')) THEN RAISE EXCEPTION 'Invalid filters';END IF;
 IF strategy NOT IN ('rsi','pin-bars','bb-pin-bars','rsi-pb','ma-cross') THEN RAISE EXCEPTION 'Invalid strategy';END IF;
 FOREACH field IN ARRAY ARRAY['strategy','asset','query','timeframe','direction','from','to'] LOOP
  IF filters ? field AND (jsonb_typeof(filters->field)<>'string' OR length(filters->>field)>256) THEN RAISE EXCEPTION 'Invalid filter';END IF;
 END LOOP;
 FOREACH field IN ARRAY ARRAY['archived','catalog'] LOOP
  IF filters ? field AND jsonb_typeof(filters->field)<>'boolean' THEN RAISE EXCEPTION 'Invalid filter';END IF;
 END LOOP;
 catalog:=coalesce((filters->>'catalog')::boolean,false);archived:=coalesce((filters->>'archived')::boolean,false);
 predicate:=format('r.strategy=%L',strategy);
 IF strategy='rsi' THEN predicate:=predicate||format(' AND NOT EXISTS (SELECT 1 FROM bot_runtime.finding_state st WHERE st.finding_id=r.id AND (st.status=''deleted'' OR (NOT %L::boolean AND st.status=''archived'')))',archived);END IF;
 -- Catalog is independent of the current asset/time/date selection.
 IF catalog THEN
  EXECUTE 'SELECT coalesce(jsonb_agg(symbol ORDER BY symbol),''[]''::jsonb) FROM (SELECT DISTINCT r.symbol FROM bot_runtime.signal_search_rows r WHERE '||predicate||' AND r.symbol IS NOT NULL) a' INTO items;
  RETURN jsonb_build_object('assets',items);
 END IF;
 FOREACH field IN ARRAY ARRAY['asset','timeframe','direction'] LOOP
  value:=nullif(filters->>field,'');
  IF field='direction' AND value NOT IN ('bullish','bearish') THEN RAISE EXCEPTION 'Invalid direction';END IF;
  IF value IS NOT NULL THEN predicate:=predicate||format(' AND r.%I=%L',CASE field WHEN 'asset' THEN 'symbol' ELSE field END,value);END IF;
 END LOOP;
 value:=nullif(trim(filters->>'query'),'');
 IF value IS NOT NULL THEN predicate:=predicate||format(' AND strpos(lower(r.symbol),lower(%L))>0',value);END IF;
 FOREACH field IN ARRAY ARRAY['from','to'] LOOP
  value:=nullif(filters->>field,'');
  IF value IS NOT NULL THEN
   IF value !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$' THEN RAISE EXCEPTION 'Invalid date';END IF;
   bound:=value::timestamp;
   -- Indexable conservative UTC bounds plus exact Israel wall-minute comparison.
   -- Both occurrences of an autumn repeated minute are included; spring gaps match nothing.
   predicate:=predicate||format(' AND r.confirmed_at %s %L::bigint AND to_char(to_timestamp(r.confirmed_at/1000.0) AT TIME ZONE ''Asia/Jerusalem'',''YYYY-MM-DD"T"HH24:MI'') %s %L',
    CASE field WHEN 'from' THEN '>=' ELSE '<' END,
    (extract(epoch FROM (bound AT TIME ZONE 'Asia/Jerusalem'))*1000)::bigint+CASE field WHEN 'from' THEN -3600000 ELSE 3660000 END,
    CASE field WHEN 'from' THEN '>=' ELSE '<=' END,value);
  END IF;
 END LOOP;
 IF nullif(filters->>'from','')>nullif(filters->>'to','') THEN RAISE EXCEPTION 'Invalid date range';END IF;
 IF filters->'cursor' IS NOT NULL AND filters->'cursor'<>'null'::jsonb THEN
  IF jsonb_typeof(filters->'cursor')<>'object' OR jsonb_typeof(filters->'cursor'->'time') IS DISTINCT FROM 'number'
   OR jsonb_typeof(filters->'cursor'->'id') IS DISTINCT FROM 'string' OR length(filters->'cursor'->>'id') NOT BETWEEN 1 AND 256 THEN RAISE EXCEPTION 'Invalid cursor';END IF;
  cursor_time:=(filters->'cursor'->>'time')::bigint;cursor_id:=filters->'cursor'->>'id';
  predicate:=predicate||format(' AND (r.confirmed_at<%L::bigint OR (r.confirmed_at=%L::bigint AND r.id>%L))',cursor_time,cursor_time,cursor_id);
 END IF;
 -- LIMIT before feedback/followup JSON; no expensive exact-count or OFFSET scan.
 IF strategy='rsi' THEN
  -- Bound each source before merging. A global sort over the UNION would force
  -- all live evidence hashes to be calculated before returning the first page.
  page_query:='(SELECT r.id,r.confirmed_at,r.payload FROM bot_runtime.signal_search_live r WHERE '||predicate||' ORDER BY r.confirmed_at DESC,r.id LIMIT 51) UNION ALL (SELECT r.id,r.confirmed_at,r.payload FROM bot_runtime.signal_search_legacy r WHERE '||predicate||' ORDER BY r.confirmed_at DESC,r.id LIMIT 51) ORDER BY confirmed_at DESC,id LIMIT 51';
 ELSE
  source_query:=format('(SELECT %L::text strategy,id,coalesce((payload::jsonb->>''confirmed_at'')::bigint,-1) confirmed_at,payload::jsonb->>''symbol'' symbol,payload::jsonb->>''timeframe'' timeframe,payload::jsonb->>''direction'' direction,payload::jsonb payload FROM bot_runtime.%I) r',strategy,
   CASE strategy WHEN 'pin-bars' THEN 'pin_bar_signals' WHEN 'bb-pin-bars' THEN 'bb_pin_bar_signals' WHEN 'rsi-pb' THEN 'rsi_pb_signals' ELSE 'ma_cross_signals' END);
  page_query:='SELECT r.id,r.confirmed_at,r.payload FROM '||source_query||' WHERE '||predicate||' ORDER BY r.confirmed_at DESC,r.id LIMIT 51';
 END IF;
 EXECUTE 'WITH page AS MATERIALIZED ('||page_query||')
 SELECT coalesce(jsonb_agg(jsonb_build_object(''id'',p.id,''time'',p.confirmed_at,''value'',p.payload'||
 CASE WHEN strategy='rsi' THEN '||jsonb_build_object(''feedback'',to_jsonb(f)-''evidence'',''archived'',coalesce(st.status,''active'')=''archived'',''state_revision'',coalesce(st.revision,0),''followup'',u.results::jsonb->''recovery'')' ELSE '' END||') ORDER BY p.confirmed_at DESC,p.id),''[]''::jsonb) FROM page p '||
 CASE WHEN strategy='rsi' THEN 'LEFT JOIN bot_runtime.finding_feedback f ON f.finding_id=p.id LEFT JOIN bot_runtime.finding_state st ON st.finding_id=p.id LEFT JOIN bot_runtime.finding_followup u ON u.finding_id=p.id' ELSE '' END INTO items;
 last_row:=items->49;
 SELECT coalesce(jsonb_agg(v->'value' ORDER BY ord),'[]') INTO result FROM jsonb_array_elements(items) WITH ORDINALITY a(v,ord) WHERE ord<=50;
 SELECT payload::jsonb INTO scan FROM bot_runtime.scanner_status WHERE id=1;
 result:=jsonb_build_object(CASE strategy WHEN 'rsi' THEN 'findings' ELSE 'signals' END,result,'has_more',jsonb_array_length(items)>50,
 'next_cursor',CASE WHEN jsonb_array_length(items)>50 THEN jsonb_build_object('time',last_row->'time','id',last_row->'id') ELSE NULL END,'total_count',NULL,'page_count',NULL);
 IF strategy='rsi' THEN RETURN result||jsonb_build_object('summary',coalesce(scan->'coverage','[]'));END IF;
 RETURN result||jsonb_build_object('strategy',strategy,'scanned_at',scan->'scanned_at',
 'coverage',coalesce((SELECT jsonb_agg(v||coalesce(v->replace(strategy,'-','_'),'{}')) FROM jsonb_array_elements(scan->'coverage') a(v)),'[]'),
 'rules',CASE strategy WHEN 'pin-bars' THEN '{"fast":8,"slow":21,"ratio":2,"max_short_wick_ratio":0.66}'::jsonb WHEN 'bb-pin-bars' THEN '{"bb_period":20,"bb_multiplier":2,"ratio":2,"max_short_wick_ratio":0.66}'::jsonb WHEN 'ma-cross' THEN '{"fast":50,"slow":200,"timeframe":"daily"}'::jsonb ELSE '{"ratio":2,"max_short_wick_ratio":0.66}'::jsonb END);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_signal_query(uuid,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_signal_query(uuid,jsonb) TO authenticated;
COMMIT;
