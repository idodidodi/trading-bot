-- Additive adapter. Apply AFTER the latest existing dashboard migrations AND
-- postgres-runtime.sql. Disabled until a manual runtime_control activation.
BEGIN;
SET search_path=bot_runtime,public,pg_catalog;

CREATE OR REPLACE VIEW bot_runtime.runtime_findings AS
 SELECT k.finding_id AS id,'live'::text AS source,s.payload::jsonb AS signal,
        s.received AS occurred,s.status,s.attempts,s.error
 FROM bot_runtime.signals s JOIN bot_runtime.finding_keys k
 ON k.source='live' AND k.evidence_hash=sha256(convert_to(s.payload::jsonb::text,'UTF8'))
 UNION ALL
 SELECT DISTINCT ON (k.finding_id) k.finding_id,'backtest',s.signal,
        b.id::float8,'saved',0::bigint,NULL::text
 FROM bot_runtime.backtest_runs b
 CROSS JOIN LATERAL jsonb_array_elements(b.report::jsonb->'results') r(result)
 CROSS JOIN LATERAL jsonb_array_elements(r.result->'signals') s(signal)
 JOIN bot_runtime.finding_keys k ON k.source='backtest'
 AND k.evidence_hash=sha256(convert_to(s.signal::text,'UTF8'))
 ORDER BY 1,4 DESC;

CREATE OR REPLACE VIEW bot_runtime.runtime_records AS
 WITH control AS (SELECT installation FROM bot_runtime.runtime_control WHERE id=1),
 records AS (
 SELECT 'config'::text kind,'current'::text key,jsonb_build_object('revision',revision,'applied_revision',applied_revision,'config',payload::jsonb) payload FROM bot_runtime.managed_config
 UNION ALL SELECT source,id,jsonb_build_object('id',id,'signal',signal,'status',status,'attempts',attempts,'error',error) FROM bot_runtime.runtime_findings
 UNION ALL SELECT l.source,l.id,l.payload::jsonb FROM bot_runtime.legacy_findings l
 WHERE NOT EXISTS(SELECT 1 FROM bot_runtime.runtime_findings f WHERE f.source=l.source AND f.id=l.id)
 UNION ALL SELECT 'feedback',finding_id,jsonb_build_object('finding_id',finding_id,'source',source,'evidence',evidence::jsonb,'rating',rating,'comment',comment,'revision',revision,'updated_at',updated_at) FROM bot_runtime.finding_feedback
 UNION ALL SELECT 'finding_state',finding_id,jsonb_build_object('finding_id',finding_id,'status',status,'revision',revision,'updated_at',updated_at) FROM bot_runtime.finding_state
 UNION ALL SELECT 'log',id::text,jsonb_build_object('occurred',occurred,'event',event,'details',details) FROM bot_runtime.activity_log
 UNION ALL SELECT 'candle','live:'||feed||':'||timeframe||':'||start,jsonb_build_object('source','live','symbol',feed,'asset',asset,'timeframe',timeframe,'start',start,'end',"end",'open',open,'high',high,'low',low,'close',close) FROM bot_runtime.candle_cache
 UNION ALL SELECT 'candle','backtest:'||feed||':'||timeframe||':'||start,jsonb_build_object('source','backtest','symbol',feed,'timeframe',timeframe,'start',start,'end',"end",'open',open,'high',high,'low',low,'close',close) FROM bot_runtime.backtest_candle_cache
 UNION ALL SELECT 'summary','scanner',payload::jsonb FROM bot_runtime.scanner_status
 UNION ALL SELECT 'summary','live',coalesce(payload::jsonb->'coverage','[]') FROM bot_runtime.scanner_status
 UNION ALL SELECT 'backtest_run',id::text,report::jsonb FROM bot_runtime.backtest_runs
 UNION ALL SELECT 'summary','backtest',coalesce(jsonb_agg((result-'signals'-'followups')||jsonb_build_object('signals',jsonb_array_length(result->'signals'))),'[]') FROM (SELECT report::jsonb FROM bot_runtime.backtest_runs ORDER BY id DESC LIMIT 1) b CROSS JOIN LATERAL jsonb_array_elements(b.report->'results') r(result)
 )
 SELECT c.installation,r.kind,r.key,r.payload,now() AS updated_at,0::bigint AS change_sequence FROM records r CROSS JOIN control c;
REVOKE ALL ON bot_runtime.runtime_findings,bot_runtime.runtime_records FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION bot_runtime.write_config_view() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=bot_runtime,pg_catalog AS $$
DECLARE old_asset jsonb; current_asset jsonb; frame text; saved_feed text;
BEGIN
 IF OLD.kind<>'config' OR OLD.key<>'current' OR NEW.kind<>OLD.kind OR NEW.key<>OLD.key THEN RAISE EXCEPTION 'Read-only projection';END IF;
 FOR old_asset IN SELECT value FROM jsonb_array_elements(OLD.payload->'config'->'assets') LOOP
  SELECT value INTO current_asset FROM jsonb_array_elements(NEW.payload->'config'->'assets') WHERE value->>'id'=old_asset->>'id';
  FOR frame IN SELECT jsonb_array_elements_text(coalesce(old_asset->'timeframes',OLD.payload->'config'->'timeframes','["monthly","weekly","daily","4h"]')) LOOP
   IF current_asset IS NULL OR NOT coalesce((current_asset->>'enabled')::boolean,true)
    OR NOT coalesce(current_asset->'timeframes',NEW.payload->'config'->'timeframes','[]') ? frame
    OR NOT coalesce(current_asset->'scan_strategies','["rsi","pin-bars","rsi-pb"]') ? 'rsi-pb'
    OR (old_asset->'provider',old_asset->'symbol',old_asset->'exchange',old_asset->'timeframe_providers'->frame)
       IS DISTINCT FROM (current_asset->'provider',current_asset->'symbol',current_asset->'exchange',current_asset->'timeframe_providers'->frame) THEN
    FOR saved_feed IN SELECT feed FROM rsi_pb_state WHERE timeframe=frame AND left(feed,length(old_asset->>'id')+1)=old_asset->>'id'||'@' LOOP
     DELETE FROM rsi_pb_pending WHERE feed=saved_feed AND timeframe=frame;
     DELETE FROM rsi_pb_state WHERE feed=saved_feed AND timeframe=frame;
    END LOOP;
   END IF;
  END LOOP;
 END LOOP;
 UPDATE managed_config SET revision=(NEW.payload->>'revision')::bigint,payload=(NEW.payload->'config')::text WHERE id=1;
 INSERT INTO config_history VALUES((NEW.payload->>'revision')::bigint,(NEW.payload->'config')::text,extract(epoch FROM now()));
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS config_projection_write ON bot_runtime.runtime_records;
CREATE TRIGGER config_projection_write INSTEAD OF UPDATE ON bot_runtime.runtime_records FOR EACH ROW EXECUTE FUNCTION bot_runtime.write_config_view();

-- Reuse the CURRENT deployed validation for asset editing and candle mapping.
-- No mirror table is written: config UPDATE goes through the direct view trigger.
DO $$DECLARE definition text;BEGIN
 definition:=pg_get_functiondef('public.dashboard_api_base(uuid,text,jsonb)'::regprocedure);
 definition:=replace(definition,'public.dashboard_api_base','bot_runtime.asset_api');
 definition:=replace(definition,'dashboard_records','bot_runtime.runtime_records');
 definition:=replace(definition,'on conflict on constraint bot_runtime.runtime_records_pkey','on conflict (installation,kind,key)');
 definition:=replace(definition,'SET search_path TO ''public'', ''pg_temp''','SET search_path TO ''bot_runtime'', ''public'', ''pg_catalog'', ''pg_temp''');
 definition:=replace(definition,' for update','');
 definition:=replace(definition,'FOR UPDATE','');
 EXECUTE definition;
END $$;
REVOKE ALL ON FUNCTION bot_runtime.asset_api(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION bot_runtime.read_routes() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT public.telegram_read_routes((SELECT installation FROM bot_runtime.runtime_control WHERE id=1));
$$;
REVOKE ALL ON FUNCTION bot_runtime.read_routes() FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION bot_runtime.momentum_opened(sig jsonb,bar jsonb) RETURNS bigint
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN sig->>'market'='stock' THEN
 (extract(epoch FROM (((to_timestamp((bar->>'start')::float8/1000) AT TIME ZONE 'America/New_York')::date+time '09:30') AT TIME ZONE 'America/New_York'))*1000)::bigint
 ELSE (bar->>'start')::bigint END;
$$;
CREATE OR REPLACE FUNCTION bot_runtime.momentum_outcome(sig jsonb,bars jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE
 baseline bigint:=greatest((sig->>'confirmed_at')::bigint,coalesce((sig->>'published_at')::bigint,0));
 b jsonb;first_bar jsonb;entry float8;opening bigint;d integer:=CASE WHEN sig->>'direction'='buy' THEN 1 ELSE -1 END;
 stop_price float8:=(sig->>'stop')::float8;target_price float8:=(sig->>'target')::float8;
 hold integer:=coalesce((sig->'rules'->>'max_hold')::int,10);cost float8:=coalesce((sig->'rules'->>'cost_bps')::float8,10);
 risk float8;reward float8;adverse float8:=0;favorable float8:=0;n integer:=0;status text;exit_price float8;
BEGIN
 SELECT value INTO first_bar FROM jsonb_array_elements(coalesce(bars,'[]')) WHERE bot_runtime.momentum_opened(sig,value)>=baseline ORDER BY (value->>'start')::bigint LIMIT 1;
 IF first_bar IS NULL THEN RETURN '{"status":"awaiting entry"}';END IF;
 entry:=(first_bar->>'open')::float8;opening:=bot_runtime.momentum_opened(sig,first_bar);
 IF sig->>'entry_open_at' IS NOT NULL AND opening<>(sig->>'entry_open_at')::bigint THEN RETURN '{"status":"unavailable","reason":"Entry-session candle missing; no later entry substituted"}';END IF;
 IF opening-baseline>(CASE WHEN sig->>'market'='stock' THEN 5 ELSE 2 END)*86400000::bigint THEN RETURN '{"status":"unavailable","reason":"Entry-session history missing; no later entry substituted"}';END IF;
 IF entry<(sig->>'entry_min')::float8 OR entry>(sig->>'entry_max')::float8 THEN RETURN jsonb_build_object('status','skipped gap','entry_at',opening);END IF;
 risk:=d*(entry-stop_price);reward:=d*(target_price-entry);
 IF risk<=0 OR reward/risk<1.5 THEN RETURN '{"status":"skipped reward/risk"}';END IF;
 FOR b IN SELECT value FROM jsonb_array_elements(bars) WHERE bot_runtime.momentum_opened(sig,value)>=baseline ORDER BY (value->>'start')::bigint LIMIT hold LOOP
  n:=n+1;
  adverse:=greatest(adverse,greatest(0,d*(entry-(b->>CASE WHEN d=1 THEN 'low' ELSE 'high' END)::float8))/entry*100);
  favorable:=greatest(favorable,greatest(0,d*((b->>CASE WHEN d=1 THEN 'high' ELSE 'low' END)::float8-entry))/entry*100);
  IF (d=1 AND (b->>'low')::float8<=stop_price) OR (d=-1 AND (b->>'high')::float8>=stop_price) THEN
   exit_price:=CASE WHEN d=1 THEN least((b->>'open')::float8,stop_price) ELSE greatest((b->>'open')::float8,stop_price) END;status:='stop';
  ELSIF (d=1 AND (b->>'high')::float8>=target_price) OR (d=-1 AND (b->>'low')::float8<=target_price) THEN exit_price:=target_price;status:='target';
  ELSIF n=hold THEN exit_price:=(b->>'close')::float8;status:='time exit';
  ELSE CONTINUE;END IF;
  RETURN jsonb_build_object('status',status,'entry_at',opening,'entry_price',entry,'exit_at',(b->>'end')::bigint,'exit_price',exit_price,'net_pct',d*(exit_price/entry-1)*100-2*cost/100,'adverse_pct',adverse,'favorable_pct',favorable);
 END LOOP;
 RETURN jsonb_build_object('status','open','entry_at',opening,'entry_price',entry,'adverse_pct',adverse,'favorable_pct',favorable);
END $$;
REVOKE ALL ON FUNCTION bot_runtime.momentum_opened(jsonb,jsonb),bot_runtime.momentum_outcome(jsonb,jsonb) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION bot_runtime.api(inst uuid,operation text,input jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=bot_runtime,public,pg_catalog,pg_temp AS $$
#variable_conflict use_variable
DECLARE
 path text:=split_part(operation,'?',1);q text:=split_part(operation,'?',2);
 source text:=coalesce(substring(q FROM 'source=([^&]+)'),'live');
 page bigint:=coalesce(substring(q FROM 'page=([^&]+)')::bigint,0);
 key text;sig jsonb;current_value jsonb;expected bigint;result jsonb;items jsonb;
 mode text;bars jsonb;windows jsonb;row_value record;prefix text;total_count bigint;
 sort_field text:=coalesce(substring(q FROM 'sort=([^&]+)'),'confirmed_at');sort_order text:=coalesce(substring(q FROM 'order=([^&]+)'),'asc');
BEGIN
 IF NOT public.dashboard_can_view(inst) OR NOT EXISTS(SELECT 1 FROM runtime_control WHERE installation=inst AND enabled) THEN RAISE EXCEPTION 'Not authorized';END IF;
 IF page<0 OR page>10000 THEN RAISE EXCEPTION 'Invalid page';END IF;
 IF input<>'{}' AND NOT public.dashboard_can_edit(inst) THEN RAISE EXCEPTION 'View-only access';END IF;
 IF path='/api/assets' THEN
  PERFORM pg_advisory_xact_lock(714269001);
  result:=bot_runtime.asset_api(inst,operation,input);
  RETURN result||jsonb_build_object('coverage',coalesce((SELECT payload::jsonb->'coverage' FROM scanner_status WHERE id=1),'[]'),
    'daily_selection',(SELECT payload::jsonb FROM daily_universe ORDER BY session DESC LIMIT 1),
    'weekly_stock_selection',(SELECT payload::jsonb FROM weekly_stock_screen ORDER BY day DESC LIMIT 1));
 ELSIF path='/api/telegram-routing' THEN
  -- The existing routing endpoint retains owner-only writes and its canonical table.
  RETURN public.dashboard_api_before_postgres(inst,operation,input);
 ELSIF path='/api/feedback' OR path='/api/finding-state' THEN
  key:=input->>'finding_id';source:=input->>'source';
  IF source NOT IN ('live','backtest') OR key IS NULL THEN RAISE EXCEPTION 'Invalid finding';END IF;
  SELECT payload->'signal' INTO sig FROM runtime_records WHERE kind=source AND runtime_records.key=key;
  IF sig IS NULL THEN RAISE EXCEPTION 'Finding not found';END IF;
  IF coalesce(input->>'revision','') !~ '^[0-9]+$' THEN RAISE EXCEPTION 'Expected revision';END IF;
  PERFORM pg_advisory_xact_lock(714269001);
  IF path='/api/feedback' THEN
   IF jsonb_typeof(input->'comment') IS DISTINCT FROM 'string' OR length(input->>'comment')>4000
     OR NOT input ? 'rating' OR (input->'rating'<>'null' AND (input->>'rating') !~ '^[1-5]$') THEN RAISE EXCEPTION 'Invalid feedback';END IF;
   SELECT revision INTO expected FROM finding_feedback WHERE finding_id=key;
   expected:=coalesce(expected,0);
   IF (input->>'revision')::bigint<>expected THEN RAISE EXCEPTION 'Feedback changed; reload';END IF;
   INSERT INTO finding_feedback VALUES(key,source,sig::text,(input->>'rating')::bigint,input->>'comment',expected+1,extract(epoch FROM now()))
     ON CONFLICT(finding_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,revision=excluded.revision,updated_at=excluded.updated_at;
   INSERT INTO feedback_history(finding_id,source,evidence,rating,comment,revision,updated_at)
    SELECT finding_id,finding_feedback.source,evidence,rating,comment,revision,updated_at FROM finding_feedback WHERE finding_id=key;
   RETURN jsonb_build_object('saved',true,'revision',expected+1,'storage','Postgres');
  ELSE
   IF source<>'live' OR coalesce(input->>'action','') NOT IN ('archive','restore','delete') THEN RAISE EXCEPTION 'Invalid signal action';END IF;
   SELECT status,revision INTO row_value FROM finding_state WHERE finding_id=key;
   expected:=coalesce(row_value.revision,0);
   IF row_value.status='deleted' OR (input->>'revision')::bigint<>expected THEN RAISE EXCEPTION 'Signal changed; reload';END IF;
   INSERT INTO finding_state VALUES(key,CASE input->>'action' WHEN 'archive' THEN 'archived' WHEN 'restore' THEN 'active' ELSE 'deleted' END,expected+1,extract(epoch FROM now()))
    ON CONFLICT(finding_id) DO UPDATE SET status=excluded.status,revision=excluded.revision,updated_at=excluded.updated_at;
   RETURN jsonb_build_object('saved',true,'revision',expected+1);
  END IF;
 ELSIF path='/api/findings' THEN
  IF source NOT IN ('live','backtest') THEN RAISE EXCEPTION 'Invalid finding source';END IF;
  IF sort_field NOT IN ('asset','timeframe','evidence','confirmed_at','rating') OR sort_order NOT IN ('asc','desc') THEN RAISE EXCEPTION 'Invalid sort';END IF;
  SELECT coalesce(jsonb_agg(value),'[]') INTO items FROM (
   SELECT r.payload||jsonb_build_object('feedback',f.payload,'archived',coalesce(s.payload->>'status','active')='archived',
    'state_revision',coalesce((s.payload->>'revision')::bigint,0),'followup',u.results::jsonb->'recovery') value
   FROM runtime_records r
   LEFT JOIN runtime_records f ON f.kind='feedback' AND f.key=r.key
   LEFT JOIN runtime_records s ON s.kind='finding_state' AND s.key=r.key
   LEFT JOIN finding_followup u ON u.finding_id=r.key
   WHERE r.kind=source AND (source<>'backtest' OR NOT EXISTS(SELECT 1 FROM backtest_runs) OR EXISTS(
    SELECT 1 FROM (SELECT report::jsonb report FROM backtest_runs ORDER BY id DESC LIMIT 1) b
    CROSS JOIN LATERAL jsonb_array_elements(b.report->'results') z(result)
    CROSS JOIN LATERAL jsonb_array_elements(z.result->'signals') x(signal)
    JOIN finding_keys k ON k.source='backtest' AND k.evidence_hash=sha256(convert_to(x.signal::text,'UTF8')) WHERE k.finding_id=r.key))
   AND coalesce(s.payload->>'status','active')<>'deleted'
    AND (substring(q FROM 'archived=([^&]+)')='true' OR coalesce(s.payload->>'status','active')<>'archived')
   ORDER BY CASE WHEN source='backtest' AND sort_order='asc' THEN public.dashboard_backtest_sort_value(r.payload->'signal',f.payload,sort_field) END ASC NULLS LAST,
    CASE WHEN source='backtest' AND sort_order='desc' THEN public.dashboard_backtest_sort_value(r.payload->'signal',f.payload,sort_field) END DESC NULLS LAST,
    (r.payload->'signal'->>'confirmed_at')::bigint DESC,r.key LIMIT 51 OFFSET page*50
  ) v;
  IF source='backtest' THEN
   SELECT coalesce(sum(jsonb_array_length(z.result->'signals')),0) INTO total_count FROM (SELECT report::jsonb report FROM backtest_runs ORDER BY id DESC LIMIT 1) b CROSS JOIN LATERAL jsonb_array_elements(b.report->'results') z(result);
  END IF;
  RETURN jsonb_build_object('total_count',total_count,'page_count',(total_count+49)/50,'findings',CASE WHEN jsonb_array_length(items)>50 THEN items-50 ELSE items END,'has_more',jsonb_array_length(items)>50,
    'summary',coalesce((SELECT payload FROM runtime_records WHERE kind='summary' AND runtime_records.key=source),'[]'),
    'scan',(SELECT payload::jsonb FROM scanner_status WHERE id=1));
 ELSIF path='/api/logs' THEN
  RETURN jsonb_build_object('rows',coalesce((SELECT jsonb_agg(value) FROM (
   SELECT jsonb_build_object('occurred',occurred,'event',event,'details',details) value FROM activity_log
   WHERE operation NOT LIKE '%filter=errors%' OR (event||' '||details) ~* 'error|failed|unavailable|insufficient|retry' ORDER BY id DESC LIMIT 200) v),'[]'));
 ELSIF path='/api/candles' THEN
  RETURN bot_runtime.asset_api(inst,operation,input);
 ELSIF path='/api/followup' THEN
  key:=substring(q FROM 'finding=([^&]+)');mode:=coalesce(substring(q FROM 'mode=([^&]+)'),'recovery');
  IF source NOT IN ('live','backtest') OR mode NOT IN ('recovery','all') THEN RAISE EXCEPTION 'Invalid followup';END IF;
  SELECT payload->'signal' INTO sig FROM runtime_records WHERE kind=source AND runtime_records.key=key;
  IF sig IS NULL OR EXISTS(SELECT 1 FROM finding_state WHERE finding_id=key AND status='deleted') THEN RAISE EXCEPTION 'Finding not found';END IF;
  SELECT coalesce(jsonb_agg(payload ORDER BY (payload->>'start')::bigint),'[]') INTO bars FROM runtime_records
   WHERE kind='candle' AND payload->>'source'=source AND payload->>'symbol'=sig->>'symbol' AND payload->>'timeframe'=sig->>'timeframe'
    AND (payload->>'start')::bigint>=(sig->>'pivot2')::bigint AND (payload->>'end')::bigint<=CASE WHEN source='live' THEN extract(epoch FROM now())*1000 ELSE coalesce((SELECT (report::jsonb->>'end_at')::bigint FROM backtest_runs ORDER BY id DESC LIMIT 1),1609459200000) END;
  windows:=jsonb_build_object('recovery',public.dashboard_calculate_followup(sig,bars,'recovery'),'all',public.dashboard_calculate_followup(sig,bars,'all'));
  INSERT INTO finding_followup VALUES(key,windows::text,extract(epoch FROM now())) ON CONFLICT(finding_id) DO UPDATE SET results=excluded.results,calculated_at=excluded.calculated_at;
  RETURN jsonb_build_object('signal',sig,'followup',windows->mode);
 ELSIF path IN ('/api/pin-bars','/api/bb-pin-bars','/api/rsi-pb') THEN
  prefix:=CASE path WHEN '/api/pin-bars' THEN 'pin_bar' WHEN '/api/rsi-pb' THEN 'rsi_pb' ELSE 'bb_pin_bar' END;
  EXECUTE format('SELECT coalesce(jsonb_agg(payload::jsonb),''[]'') FROM (SELECT payload FROM bot_runtime.%I ORDER BY confirmed_at DESC,id LIMIT 51 OFFSET $1) v',prefix||'_signals') INTO items USING page*50;
  SELECT payload::jsonb INTO current_value FROM scanner_status WHERE id=1;
  RETURN jsonb_build_object('strategy',substring(path FROM 6),'signals',CASE WHEN jsonb_array_length(items)>50 THEN items-50 ELSE items END,
   'has_more',jsonb_array_length(items)>50,'coverage',coalesce((SELECT jsonb_agg(v||coalesce(v->CASE prefix WHEN 'pin_bar' THEN 'pin_bars' WHEN 'rsi_pb' THEN 'rsi_pb' ELSE 'bb_pin_bars' END,'{}')) FROM jsonb_array_elements(current_value->'coverage') a(v)),'[]'),'scanned_at',current_value->'scanned_at',
   'rules',CASE WHEN prefix='pin_bar' THEN '{"fast":8,"slow":21,"ratio":2,"max_short_wick_ratio":0.66}'::jsonb WHEN prefix='rsi_pb' THEN '{"max_short_wick_ratio": 0.66, "ratio": 2.0, "rsi": {"bb_multiplier": 2.0, "bb_period": 20, "max_spacing": 60, "min_spacing": 2, "pivot_left": 2, "pivot_right": 1, "price_source": "close", "rsi_period": 3, "rsi_tolerance": 1.0, "skill_hash": "b4b8c578656c86fb"}, "seed": "RSI candle-close divergence; next candle only; opposite wick shape; no EMA filters", "version": "rsi-pb-v1-242ec67c5c9037c1"}'::jsonb ELSE '{"bb_period":20,"bb_multiplier":2,"ratio":2,"max_short_wick_ratio":0.66}'::jsonb END);
 ELSIF path='/api/catchup' THEN
  PERFORM pg_advisory_xact_lock(714269001);
  IF input<>'{}' THEN
   IF input->>'action' IS DISTINCT FROM 'request' THEN RAISE EXCEPTION 'Invalid scanner action';END IF;
   SELECT id INTO key FROM scanner_commands WHERE status IN ('pending','running') ORDER BY created LIMIT 1;
   IF key IS NULL THEN key:=gen_random_uuid()::text;INSERT INTO scanner_commands VALUES(key,'pending','{}',extract(epoch FROM now()));END IF;
  ELSE SELECT id INTO key FROM scanner_commands ORDER BY created DESC LIMIT 1;
  END IF;
  SELECT payload::jsonb||jsonb_build_object('id',id,'status',status) INTO result FROM scanner_commands WHERE id=key;
  RETURN coalesce(result,'{"status":"idle"}');
 ELSIF path='/api/csv-backtests' THEN
  IF input<>'{}' THEN
   IF input<>'{"action":"authorize"}'::jsonb THEN RAISE EXCEPTION 'Invalid CSV operation';END IF;
   RETURN '{"authorized":true}';
  END IF;
  key:=substring(q FROM '[?&]?id=([^&]+)');
  SELECT report::jsonb INTO result FROM csv_backtest_runs WHERE (key IS NULL OR id=key)
   AND (substring(q FROM 'strategy=([^&]+)') IS NULL OR report::jsonb->>'strategy'=substring(q FROM 'strategy=([^&]+)')) ORDER BY created DESC LIMIT 1;
  IF result IS NOT NULL THEN
   SELECT coalesce(jsonb_agg(value ORDER BY ord),'[]') INTO items FROM jsonb_array_elements(result->'signals') WITH ORDINALITY a(value,ord) WHERE ord>page*50 AND ord<=(page+1)*50;
   result:=result||jsonb_build_object('signals',items,'has_more',jsonb_array_length(result->'signals')>(page+1)*50);
  END IF;
  SELECT coalesce(jsonb_agg(report::jsonb-'signals'),'[]') INTO items FROM (SELECT report FROM csv_backtest_runs ORDER BY created DESC LIMIT 50) v;
  RETURN jsonb_build_object('report',result,'reports',items);
 ELSIF path='/api/backtests' THEN
  IF input<>'{}' THEN
   IF coalesce(input->>'strategy','') NOT IN ('confirmed','warmup') OR jsonb_typeof(input->'assets') IS DISTINCT FROM 'array' OR jsonb_typeof(input->'timeframes') IS DISTINCT FROM 'array'
    OR jsonb_array_length(input->'assets') NOT BETWEEN 1 AND 100 OR jsonb_array_length(input->'timeframes') NOT BETWEEN 1 AND 5
    OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(input->'timeframes') t WHERE t NOT IN ('monthly','weekly','daily','4h','1h'))
    OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(input->'assets') a WHERE NOT EXISTS(SELECT 1 FROM backtest_candle_cache WHERE feed=a)) THEN RAISE EXCEPTION 'Invalid stored replay selection';END IF;
   PERFORM pg_advisory_xact_lock(714269004);
   IF EXISTS(SELECT 1 FROM backtest_jobs WHERE status IN ('pending','running')) THEN RAISE EXCEPTION 'Replay already active';END IF;
   key:=gen_random_uuid()::text;
   INSERT INTO backtest_jobs VALUES(key,'running',(input||'{"source":"csv","executor":"edge"}')::text,extract(epoch FROM now()));
   RETURN jsonb_build_object('id',key,'status','running','runtime',true,'executor','edge');
  END IF;
  UPDATE backtest_jobs SET status='failed',payload=jsonb_set(payload::jsonb,'{error}','"Hosted replay interrupted; retry"')::text WHERE status='running' AND payload::jsonb->>'executor'='edge' AND updated<extract(epoch FROM now())-1200;
  SELECT jsonb_build_object('id',id,'status',status,'input',payload::jsonb,'updated',updated) INTO result FROM backtest_jobs WHERE substring(q FROM 'job=([^&]+)') IS NULL OR id=substring(q FROM 'job=([^&]+)') ORDER BY updated DESC LIMIT 1;
  RETURN jsonb_build_object('assets',coalesce((SELECT jsonb_agg(DISTINCT feed) FROM backtest_candle_cache),'[]'),'csv_assets',coalesce((SELECT jsonb_agg(DISTINCT feed) FROM backtest_candle_cache),'[]'),
   'sources','["csv"]'::jsonb,'timeframes','["monthly","weekly","daily","4h","1h"]'::jsonb,'year',coalesce((SELECT report::jsonb->'year' FROM backtest_runs ORDER BY id DESC LIMIT 1),'2020'),
   'strategies','{"confirmed":"RSI divergence: structural confirmation","warmup":"RSI divergence: pivot-close candidate"}'::jsonb,'job',result);
 ELSIF path='/api/momentum' THEN
  IF source='backtest' THEN RETURN coalesce((SELECT payload::jsonb FROM momentum_reports ORDER BY id DESC LIMIT 1),'{"signals":[],"metrics":{},"note":"Awaiting replay"}');END IF;
  IF source<>'live' THEN RAISE EXCEPTION 'Invalid source';END IF;
  SELECT coalesce(jsonb_agg(value||jsonb_build_object('published_day',day,'paper',p.payload::jsonb,'outcome',bot_runtime.momentum_outcome(value,h.payload::jsonb))),'[]') INTO items FROM
   (SELECT * FROM momentum_runs ORDER BY day DESC LIMIT 30) r
   CROSS JOIN LATERAL jsonb_each(r.payload::jsonb->'picks') s(k,value)
   LEFT JOIN momentum_history h ON h.market=value->>'market' AND h.symbol=value->>'symbol'
   LEFT JOIN paper_trades p ON p.payload::jsonb->'signal'->>'symbol'=value->>'symbol' AND p.payload::jsonb->'signal'->'confirmed_at'=value->'confirmed_at'
   WHERE value<>'null';
  RETURN jsonb_build_object('latest',(SELECT payload::jsonb FROM momentum_runs ORDER BY day DESC LIMIT 1),'signals',items,
   'paper',jsonb_build_object('trades',coalesce((SELECT jsonb_agg(payload::jsonb) FROM paper_trades),'[]'),'status',(SELECT payload::jsonb FROM paper_status WHERE id=1)),
   'note','Daily candidates and authoritative paper execution history.');
 ELSIF path='/api/versions' THEN
  RETURN jsonb_build_object('environment',coalesce((SELECT payload::jsonb->>'environment' FROM scanner_status WHERE id=1),'prod'),'database','Postgres','cloud_sync_enabled',false,'local',coalesce((SELECT payload::jsonb->'versions' FROM scanner_status WHERE id=1),jsonb_build_object('version','Worker not started')),'updates',jsonb_build_object('supported',false,'message','Manual migration release'),'checked_at',extract(epoch FROM now()));
 END IF;
 RAISE EXCEPTION 'Unknown runtime operation';
END $$;
REVOKE ALL ON FUNCTION bot_runtime.api(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;

-- Public entrypoint retains original behavior until EXPLICIT manual activation.
DO $$BEGIN
 IF to_regprocedure('public.dashboard_api_before_postgres(uuid,text,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_api(uuid,text,jsonb) RENAME TO dashboard_api_before_postgres;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_api(installation uuid,operation text,input jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=$1 AND c.enabled) THEN
  RETURN bot_runtime.api($1,$2,$3);
 END IF;
 RETURN public.dashboard_api_before_postgres($1,$2,$3);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_api_before_postgres(uuid,text,jsonb) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.dashboard_api(uuid,text,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.dashboard_api(uuid,text,jsonb) TO authenticated;

-- Existing CSV edge function now saves directly to the runtime table when enabled.
DO $$BEGIN
 IF to_regprocedure('public.dashboard_save_csv_before_postgres(uuid,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_save_csv_backtest(uuid,jsonb) RENAME TO dashboard_save_csv_before_postgres;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_save_csv_backtest(installation uuid,report jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=$1 AND c.enabled) THEN
  IF coalesce(report->>'id','') !~ '^[a-f0-9]{64}$' OR jsonb_typeof(report->'signals') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Invalid report';END IF;
  INSERT INTO bot_runtime.csv_backtest_runs VALUES(report->>'id',coalesce((report->>'generated_at')::bigint,(extract(epoch FROM now())*1000)::bigint),report::text,NULL)
   ON CONFLICT(id) DO UPDATE SET report=excluded.report,created=excluded.created;
  RETURN '{"saved":true}';
 END IF;
 RETURN public.dashboard_save_csv_before_postgres($1,$2);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_save_csv_before_postgres(uuid,jsonb),public.dashboard_save_csv_backtest(uuid,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_save_csv_backtest(uuid,jsonb) TO service_role;

-- Fence the retired mirror endpoint after activation. Old installations must be
-- stopped manually too: they cannot honor the new worker ownership lock.
DO $$BEGIN
 IF to_regprocedure('public.dashboard_ingest_before_postgres(uuid,jsonb,integer)') IS NULL THEN
  ALTER FUNCTION public.dashboard_ingest(uuid,jsonb,integer) RENAME TO dashboard_ingest_before_postgres;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_ingest(installation uuid,records jsonb,config_base integer DEFAULT 0)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=$1 AND c.enabled) THEN RAISE EXCEPTION 'Mirroring retired for this installation';END IF;
 RETURN public.dashboard_ingest_before_postgres($1,$2,$3);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_ingest_before_postgres(uuid,jsonb,integer),public.dashboard_ingest(uuid,jsonb,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_ingest(uuid,jsonb,integer) TO service_role;
-- Independent hosted replay reads the same canonical candles as Python.
CREATE OR REPLACE FUNCTION public.dashboard_replay_history(installation uuid,kind text,symbol text DEFAULT NULL,timeframe text DEFAULT NULL,page_offset integer DEFAULT 0)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
#variable_conflict use_variable
DECLARE result jsonb;
BEGIN
 IF kind NOT IN ('backtest_run','candle') OR page_offset<0 OR page_offset>10000000 THEN RAISE EXCEPTION 'Invalid history request';END IF;
 IF EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=$1 AND c.enabled) THEN
  IF kind='backtest_run' THEN
   SELECT coalesce(jsonb_agg(jsonb_build_object('payload',report::jsonb)),'[]') INTO result FROM (SELECT report FROM bot_runtime.backtest_runs ORDER BY id DESC LIMIT 1) v;
  ELSE
   SELECT coalesce(jsonb_agg(jsonb_build_object('payload',jsonb_build_object('source','backtest','symbol',feed,'timeframe',v.timeframe,'start',start,'end',"end",'open',open,'high',high,'low',low,'close',close))),'[]') INTO result FROM
    (SELECT * FROM bot_runtime.backtest_candle_cache c WHERE c.feed=symbol AND c.timeframe=dashboard_replay_history.timeframe ORDER BY start LIMIT 1000 OFFSET page_offset) v;
  END IF;
 ELSE
  SELECT coalesce(jsonb_agg(jsonb_build_object('payload',payload)),'[]') INTO result FROM (
   SELECT r.payload FROM public.dashboard_records r WHERE r.installation=$1 AND r.kind=dashboard_replay_history.kind
   AND (kind='backtest_run' OR (r.payload->>'source'='backtest' AND r.payload->>'symbol'=symbol AND r.payload->>'timeframe'=timeframe))
   ORDER BY CASE WHEN kind='candle' THEN (r.payload->>'start')::numeric END,r.updated_at DESC LIMIT CASE WHEN kind='backtest_run' THEN 1 ELSE 1000 END OFFSET page_offset) v;
 END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.dashboard_replay_history(uuid,text,text,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_replay_history(uuid,text,text,text,integer) TO service_role;

DO $$BEGIN
 IF to_regprocedure('public.dashboard_finish_before_postgres(uuid,uuid,jsonb,jsonb)') IS NULL THEN
  ALTER FUNCTION public.dashboard_finish_backtest(uuid,uuid,jsonb,jsonb) RENAME TO dashboard_finish_before_postgres;
  ALTER FUNCTION public.dashboard_fail_backtest(uuid,uuid,text) RENAME TO dashboard_fail_before_postgres;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_finish_backtest(installation uuid,job_id uuid,summary jsonb,findings jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=bot_runtime,public,pg_catalog,pg_temp AS $$
#variable_conflict use_variable
DECLARE item jsonb;existing text;sig jsonb;normalized jsonb:='[]';report jsonb;results jsonb;metadata jsonb;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM runtime_control c WHERE c.installation=$1 AND c.enabled) THEN RETURN public.dashboard_finish_before_postgres($1,$2,$3,$4);END IF;
 PERFORM 1 FROM backtest_jobs WHERE id=job_id::text AND status='running' AND payload::jsonb->>'executor'='edge' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Hosted job is not running';END IF;
 IF jsonb_typeof(findings) IS DISTINCT FROM 'array' OR jsonb_typeof(summary) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Invalid replay result';END IF;
 FOR item IN SELECT * FROM jsonb_array_elements(findings) LOOP
  sig:=item->'signal';
  -- Prefer the durable identity of semantically matching existing evidence.
  SELECT f.id,f.signal INTO existing,sig FROM runtime_findings f WHERE f.source='backtest'
   AND f.signal=item->'signal' LIMIT 1;
  IF existing IS NULL THEN existing:=item->>'id';sig:=item->'signal';END IF;
  IF existing IS NULL OR existing !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'Invalid finding identity';END IF;
  INSERT INTO finding_keys VALUES('backtest',sha256(convert_to(sig::text,'UTF8')),existing) ON CONFLICT DO NOTHING;
  SELECT finding_id INTO existing FROM finding_keys WHERE source='backtest' AND evidence_hash=sha256(convert_to(sig::text,'UTF8'));
  normalized:=normalized||jsonb_build_array(item||jsonb_build_object('id',existing,'signal',sig));
  IF item->'followups' IS NOT NULL THEN
   INSERT INTO finding_followup VALUES(existing,(item->'followups')::text,extract(epoch FROM now()))
   ON CONFLICT(finding_id) DO UPDATE SET results=excluded.results,calculated_at=excluded.calculated_at;
  END IF;
 END LOOP;
 SELECT b.report::jsonb INTO metadata FROM backtest_runs b ORDER BY b.id DESC LIMIT 1;
 SELECT coalesce(jsonb_agg((r-'signals')||jsonb_build_object('signals',coalesce((SELECT jsonb_agg(i->'signal') FROM jsonb_array_elements(normalized) i WHERE i->'signal'->>'symbol'=r->>'asset' AND i->'signal'->>'timeframe'=r->>'timeframe'),'[]'))),'[]') INTO results FROM jsonb_array_elements(summary) r;
 report:=jsonb_build_object('year',coalesce(metadata->'year','2020'),'start_at',coalesce(metadata->'start_at','1577836800000'),
  'end_at',coalesce(metadata->'end_at','1609459200000'),'rules',coalesce(metadata->'rules','{}'),'results',results,'executor','edge');
 INSERT INTO backtest_runs(report) VALUES(report::text);
 UPDATE backtest_jobs SET status='completed',updated=extract(epoch FROM now()) WHERE id=job_id::text;
 RETURN '{"completed":true}';
END $$;
CREATE OR REPLACE FUNCTION public.dashboard_fail_backtest(installation uuid,job_id uuid,reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=$1 AND c.enabled) THEN
  UPDATE bot_runtime.backtest_jobs SET status='failed',payload=jsonb_set(payload::jsonb,'{error}',to_jsonb(left(reason,500)))::text,updated=extract(epoch FROM now()) WHERE id=job_id::text AND status='running';
  RETURN '{"failed":true}';
 END IF;
 RETURN public.dashboard_fail_before_postgres($1,$2,$3);
END $$;
REVOKE ALL ON FUNCTION public.dashboard_finish_before_postgres(uuid,uuid,jsonb,jsonb),public.dashboard_fail_before_postgres(uuid,uuid,text),public.dashboard_finish_backtest(uuid,uuid,jsonb,jsonb),public.dashboard_fail_backtest(uuid,uuid,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.dashboard_finish_backtest(uuid,uuid,jsonb,jsonb),public.dashboard_fail_backtest(uuid,uuid,text) TO service_role;

DO $$BEGIN
 IF to_regprocedure('public.telegram_coordinate_before_postgres(uuid,jsonb)') IS NULL THEN
  ALTER FUNCTION public.telegram_coordinate(uuid,jsonb) RENAME TO telegram_coordinate_before_postgres;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.telegram_coordinate(installation uuid,input jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM bot_runtime.runtime_control c WHERE c.installation=$1 AND c.enabled) THEN
  RAISE EXCEPTION 'Legacy Telegram coordinator retired for this installation';
 END IF;
 RETURN public.telegram_coordinate_before_postgres($1,$2);
END $$;
REVOKE ALL ON FUNCTION public.telegram_coordinate_before_postgres(uuid,jsonb),public.telegram_coordinate(uuid,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.telegram_coordinate(uuid,jsonb) TO service_role;
COMMIT;
