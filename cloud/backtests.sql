-- Additive migration. Run after schema.sql; preserves synced evidence and feedback.
create table if not exists public.dashboard_backtest_jobs (
 id uuid primary key default gen_random_uuid(), installation uuid not null references dashboard_installations(id),
 status text not null check(status in ('running','completed','failed')), input jsonb not null,
 summary jsonb, finding_ids jsonb, error text, updated_at timestamptz not null default now()
);
alter table dashboard_backtest_jobs enable row level security;
revoke all on dashboard_backtest_jobs from anon,authenticated;
create unique index if not exists one_running_backtest on dashboard_backtest_jobs(installation) where status='running';
-- Preserve the deployed owner-scoped API behind the new dispatcher (idempotent).
do $$ begin
 if to_regprocedure('public.dashboard_api_base(uuid,text,jsonb)') is null then
 alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_base;
 end if;
end $$;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare job dashboard_backtest_jobs; assets jsonb; result jsonb; source text; page integer; path text:=split_part(operation,'?',1);
begin
 if not exists(select 1 from dashboard_installations i where i.id=installation and i.owner_id=auth.uid()) then raise exception 'Not authorized';end if;
 if path='/api/backtests' then
 select coalesce(jsonb_agg(distinct r.payload->>'symbol'),'[]') into assets from dashboard_records r where r.installation=installation and r.kind='candle' and r.payload->>'source'='backtest';
 update dashboard_backtest_jobs j set status='failed',error='Replay timed out; retry',updated_at=now() where j.installation=installation and j.status='running' and j.updated_at<now()-interval '5 minutes';
 if input<>'{}' then
 if coalesce(input->>'strategy','') not in ('confirmed','warmup') or jsonb_typeof(input->'assets') is distinct from 'array' or jsonb_typeof(input->'timeframes') is distinct from 'array' then raise exception 'Invalid replay configuration';end if;
 if jsonb_array_length(input->'assets') not between 1 and 100 or jsonb_array_length(input->'timeframes') not between 1 and 4 or not (assets @> (input->'assets')) or exists(select 1 from jsonb_array_elements_text(input->'timeframes') t where t not in ('monthly','weekly','daily','4h')) then raise exception 'Select synced historical assets and supported timeframes';end if;
 update dashboard_backtest_jobs j set status='failed',error='Replay timed out; retry',updated_at=now() where j.installation=installation and j.status='running' and j.updated_at<now()-interval '5 minutes';
 if exists(select 1 from dashboard_backtest_jobs j where j.installation=installation and j.status='running') then return jsonb_build_object('error','A cloud backtest is already running');end if;
 insert into dashboard_backtest_jobs(installation,status,input) values(installation,'running',input) returning * into job;
 return jsonb_build_object('id',job.id,'status',job.status);
 end if;
 select * into job from dashboard_backtest_jobs j where j.installation=installation and (operation not like '%job=%' or j.id::text=substring(operation from 'job=([a-f0-9-]+)')) order by j.updated_at desc limit 1;
 return jsonb_build_object('assets',assets,'timeframes',jsonb_build_array('monthly','weekly','daily','4h'),'year',2020,'strategies',jsonb_build_object('confirmed','RSI(3), Wilder; Bollinger Bands(20, 2 population deviations). Consecutive strict pivots: 2 left / 1 right; spacing 5–60. Opposing price and RSI with a band touch at P2; confirmed at the following candle close.','warmup','Same indicators and spacing, evaluated at potential P2 close using preceding candles only. Warm-up warning; structural confirmation is pending.'),'job',case when job.id is null then null else to_jsonb(job) end);
 elsif path='/api/findings' and operation like '%source=backtest%' then
 select * into job from dashboard_backtest_jobs j where j.installation=installation and j.status='completed' order by j.updated_at desc limit 1;
 if job.id is not null then
 page:=coalesce(substring(operation from 'page=([0-9]+)')::integer,0);if page>10000 then raise exception 'Invalid page';end if;
 select coalesce(jsonb_agg(value),'[]') into result from (select r.payload||jsonb_build_object('feedback',f.payload) value from dashboard_records r left join dashboard_records f on f.installation=r.installation and f.kind='feedback' and f.key=r.key where r.installation=installation and r.kind='backtest' and job.finding_ids ? r.key order by (r.payload->'signal'->>'confirmed_at')::bigint desc,r.key limit 51 offset page*50) v;
 return jsonb_build_object('summary',job.summary,'findings',case when jsonb_array_length(result)>50 then result-50 else result end,'has_more',jsonb_array_length(result)>50);
 end if;
 end if;
 if path='/api/findings' and operation like '%source=live%' then
 result:=dashboard_api_base(installation,operation,input);
 return jsonb_set(result,'{summary}',coalesce((select r.payload from dashboard_records r where r.installation=installation and r.kind='summary' and r.key='live'),'[]'::jsonb));
 elsif path='/api/logs' then
 return jsonb_build_object('rows',coalesce((select jsonb_agg(value) from (select r.payload value from dashboard_records r where r.installation=installation and r.kind='log' and (operation not like '%filter=errors%' or ((r.payload->>'event')||' '||(r.payload->>'details')) ~* 'error|failed|unavailable|insufficient|retry') order by (r.payload->>'occurred')::double precision desc limit 200) v),'[]'::jsonb));
 end if;
 return dashboard_api_base(installation,operation,input);
end $$;
revoke all on function dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function dashboard_api(uuid,text,jsonb) to authenticated;
create or replace function public.dashboard_finish_backtest(installation uuid,job_id uuid,summary jsonb,findings jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare item jsonb; existing text; ids jsonb:='[]';
begin
 perform 1 from dashboard_backtest_jobs j where j.id=job_id and j.installation=installation and j.status='running' for update;
 if not found then raise exception 'Job is not running';end if;
 for item in select * from jsonb_array_elements(findings) loop
 -- Preserve Python finding IDs and feedback when the port reproduces existing evidence.
 select r.key into existing from dashboard_records r where r.installation=installation and r.kind='backtest'
 and r.payload->'signal'->>'symbol'=item->'signal'->>'symbol' and r.payload->'signal'->>'timeframe'=item->'signal'->>'timeframe'
 and r.payload->'signal'->>'direction'=item->'signal'->>'direction' and r.payload->'signal'->'pivot1'=item->'signal'->'pivot1'
 and r.payload->'signal'->'pivot2'=item->'signal'->'pivot2' and r.payload->'signal'->'confirmed_at'=item->'signal'->'confirmed_at'
 and r.payload->'signal'->'rules'=item->'signal'->'rules' and (r.payload->'signal'->>'signal_status') is not distinct from (item->'signal'->>'signal_status')
 and not exists(select 1 from unnest(array['price1','price2','rsi1','rsi2','band1','band2']) k where abs((r.payload->'signal'->>k)::double precision-(item->'signal'->>k)::double precision)>1e-9) limit 1;
 if existing is null then existing:=item->>'id';insert into dashboard_records(installation,kind,key,payload) values(installation,'backtest',existing,item) on conflict on constraint dashboard_records_pkey do nothing;end if;
 ids:=ids||jsonb_build_array(existing);
 end loop;
 update dashboard_backtest_jobs j set status='completed',summary=dashboard_finish_backtest.summary,finding_ids=ids,updated_at=now() where j.id=job_id and j.installation=installation;
 return jsonb_build_object('completed',true);
end $$;
create or replace function public.dashboard_fail_backtest(installation uuid,job_id uuid,reason text)
returns jsonb language sql security definer set search_path=public,pg_temp as $$
 update dashboard_backtest_jobs j set status='failed',error=left(reason,500),updated_at=now() where j.id=job_id and j.installation=installation and j.status='running' returning jsonb_build_object('failed',true);
$$;
revoke all on function dashboard_finish_backtest(uuid,uuid,jsonb,jsonb),dashboard_fail_backtest(uuid,uuid,text) from public,anon,authenticated;
grant execute on function dashboard_finish_backtest(uuid,uuid,jsonb,jsonb),dashboard_fail_backtest(uuid,uuid,text) to service_role;
