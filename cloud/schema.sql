-- Run in a Supabase project. Set up one installation row owned by your Auth user.
create table if not exists public.dashboard_installations (
 id uuid primary key, owner_id uuid not null references auth.users(id)
);
create table if not exists public.dashboard_records (
 installation uuid not null references public.dashboard_installations(id),
 kind text not null, key text not null, payload jsonb not null,
 updated_at timestamptz not null default now(), primary key(installation,kind,key)
);
alter table public.dashboard_installations enable row level security;
alter table public.dashboard_records enable row level security;
create policy owner_installation_read on public.dashboard_installations for select to authenticated using(owner_id=auth.uid());
create policy owner_record_read on public.dashboard_records for select to authenticated using(exists(select 1 from public.dashboard_installations i where i.id=installation and i.owner_id=auth.uid()));
revoke all on public.dashboard_records from anon,authenticated;
grant select on public.dashboard_records,public.dashboard_installations to authenticated;

create or replace function public.dashboard_api(installation uuid, operation text, input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare
 inst uuid := installation; path text := split_part(operation,'?',1); q text := split_part(operation,'?',2);
 source text; page integer; cfg jsonb; payload jsonb; expected integer; asset jsonb; item jsonb; entries jsonb; results jsonb:='[]'; found boolean; ticker text; key text; sig jsonb; fb jsonb; candles jsonb; center bigint; target text; mapping text; result jsonb;
begin
 if not exists(select 1 from dashboard_installations i where i.id=inst and i.owner_id=auth.uid()) then raise exception 'Not authorized';end if;
 if path='/api/assets' then
  select r.payload into cfg from dashboard_records r where r.installation=inst and r.kind='config' and r.key='current' for update;
  if cfg is null then return jsonb_build_object('error','Waiting for first local sync');end if;
  if input='{}' then return cfg;end if;
  if coalesce(jsonb_typeof(input->'revision'),'')<>'number' then raise exception 'Expected revision';end if;
  expected:=(input->>'revision')::integer;
  if expected<>(cfg->>'revision')::integer then return jsonb_build_object('error','Assets changed. Reload before saving.');end if;
  entries:=cfg->'config'->'assets';
  if input->>'action'='bulk' then
   if length(input->>'tickers')>10000 or array_length(string_to_array(input->>'tickers',','),1)>100 then raise exception 'Batch too large';end if;
   for ticker in select distinct upper(trim(x)) from unnest(string_to_array(input->>'tickers',',')) x where trim(x)<>'' loop
    if ticker !~ '^[A-Za-z0-9_^=:.!/\-]{1,100}$' then results:=results||jsonb_build_array(jsonb_build_object('ticker',ticker,'status','invalid'));continue;end if;
    if exists(select 1 from jsonb_array_elements(entries) a where upper(a->>'id')=ticker) then results:=results||jsonb_build_array(jsonb_build_object('ticker',ticker,'status','already added'));continue;end if;
    if jsonb_array_length(entries)>=100 then raise exception 'Asset limit reached';end if;
    entries:=entries||jsonb_build_array(jsonb_build_object('id',ticker,'provider','csv','path','data/candles/{asset}-{timeframe}.csv','enabled',false,'timeframes',jsonb_build_array('monthly','weekly','daily','4h'),'tradingview_symbol',''));
    results:=results||jsonb_build_array(jsonb_build_object('ticker',ticker,'status','added as disabled draft'));
   end loop;
  elsif input->>'action'='save' then
   asset:=input->'asset';
   if asset->>'id' !~ '^[A-Za-z0-9_^=:.!/\-]{1,100}$' or asset->>'provider' not in ('csv','twelvedata') or coalesce(jsonb_typeof(asset->'enabled'),'')<>'boolean' or coalesce(jsonb_typeof(asset->'timeframes'),'')<>'array' then raise exception 'Invalid asset';end if;
   if jsonb_array_length(asset->'timeframes')=0 or exists(select 1 from jsonb_array_elements_text(asset->'timeframes') f where f not in ('monthly','weekly','daily','4h')) then raise exception 'Invalid timeframes';end if;
   for key in select unnest(array['symbol','exchange','tradingview_symbol']) loop
    if coalesce(asset->>key,'')<>'' and asset->>key !~ '^[A-Za-z0-9_^=:.!/\-]{1,100}$' then raise exception 'Invalid feed mapping';end if;
   end loop;
   if coalesce(asset->>'tradingview_symbol','')<>'' and position(':' in asset->>'tradingview_symbol')=0 then raise exception 'TradingView symbol needs exchange';end if;
   if asset->>'provider'='twelvedata' and (asset->>'enabled')::boolean and (coalesce(asset->>'symbol','')='' or coalesce(asset->>'feed_confirmed','false')<>'true') then raise exception 'Confirm exact feed';end if;
   found:=false;payload:='[]';
   for item in select * from jsonb_array_elements(entries) loop
    if item->>'id'=asset->>'id' then
     found:=true;item:=item||jsonb_build_object('provider',asset->'provider','enabled',asset->'enabled','timeframes',asset->'timeframes','symbol',coalesce(asset->'symbol','""'::jsonb),'exchange',coalesce(asset->'exchange','""'::jsonb),'tradingview_symbol',coalesce(asset->'tradingview_symbol','""'::jsonb),'feed_confirmed',coalesce(asset->'feed_confirmed','false'::jsonb));
    end if;payload:=payload||jsonb_build_array(item);
   end loop;
   if not found then raise exception 'Ticker not found';end if;entries:=payload;
   results:=jsonb_build_array(jsonb_build_object('ticker',asset->>'id','status','saved'));
  else raise exception 'Invalid action';end if;
  cfg:=jsonb_set(cfg,'{config,assets}',entries);cfg:=jsonb_set(cfg,'{revision}',to_jsonb(expected+1));
  update dashboard_records r set payload=cfg,updated_at=now(),change_sequence=default where r.installation=inst and r.kind='config' and r.key='current';
  return cfg||jsonb_build_object('results',results);
 elsif path='/api/findings' then
  source:=coalesce(substring(q from 'source=([^&]+)'),'live');page:=coalesce(substring(q from 'page=([0-9]+)')::integer,0);
  if source not in ('live','backtest') or page>10000 then raise exception 'Invalid page';end if;
  select coalesce(jsonb_agg(value),'[]') into result from (select r.payload||jsonb_build_object('feedback',f.payload) value from dashboard_records r left join dashboard_records f on f.installation=r.installation and f.kind='feedback' and f.key=r.key where r.installation=inst and r.kind=source order by (r.payload->'signal'->>'confirmed_at')::bigint desc,r.key limit 51 offset page*50) v;
  return jsonb_build_object('findings',case when jsonb_array_length(result)>50 then result-50 else result end,'has_more',jsonb_array_length(result)>50,'summary',coalesce((select r.payload from dashboard_records r where r.installation=inst and r.kind='summary' and r.key='backtest'),'[]'));
 elsif path='/api/logs' then
  return jsonb_build_object('rows',coalesce((select jsonb_agg(value) from (select r.payload value from dashboard_records r where r.installation=inst and r.kind='log' order by r.updated_at desc limit 100) v),'[]'));
 elsif path='/api/feedback' then
  source:=input->>'source';key:=input->>'finding_id';
  if coalesce(jsonb_typeof(input->'revision'),'')<>'number' or coalesce(jsonb_typeof(input->'comment'),'')<>'string' or not input ? 'rating' then raise exception 'Invalid feedback';end if;
  if input->'rating'<>'null' and (jsonb_typeof(input->'rating')<>'number' or (input->>'rating') !~ '^[1-5]$') then raise exception 'Invalid rating';end if;
  select r.payload->'signal' into sig from dashboard_records r where r.installation=inst and r.kind=source and r.key=key;
  if sig is null or source not in ('live','backtest') then raise exception 'Finding not found';end if;
  if jsonb_typeof(input->'comment')<>'string' or length(input->>'comment')>4000 or (input->'rating'<>'null' and (input->>'rating')::integer not between 1 and 5) then raise exception 'Invalid feedback';end if;
  perform pg_advisory_xact_lock(hashtext(inst::text||key));
  select r.payload into fb from dashboard_records r where r.installation=inst and r.kind='feedback' and r.key=key;
  expected:=coalesce((fb->>'revision')::integer,0);
  if (input->>'revision')::integer<>expected then return jsonb_build_object('error','Feedback changed in another session. Reload.');end if;
  fb:=input||jsonb_build_object('revision',expected+1,'evidence',sig,'updated_at',extract(epoch from now()),'author',auth.uid());
  insert into dashboard_records(installation,kind,key,payload,updated_at) values(inst,'feedback',key,fb,now()) on conflict on constraint dashboard_records_pkey do update set payload=excluded.payload,updated_at=now(),change_sequence=default;
  insert into dashboard_records(installation,kind,key,payload,updated_at) values(inst,'feedback_history',key||':'||(expected+1)::text,fb,now());
  return jsonb_build_object('saved',true,'revision',expected+1,'storage','online');
 elsif path='/api/candles' then
  source:=substring(q from 'source=([^&]+)');key:=substring(q from 'finding=([^&]+)');target:=coalesce(substring(q from 'target=([^&]+)'),'pivot2');
  if target not in ('pivot1','pivot2','confirmation') then raise exception 'Invalid target';end if;
  select r.payload->'signal' into sig from dashboard_records r where r.installation=inst and r.kind=source and r.key=key;
  if sig is null then raise exception 'Finding not found';end if;
  center:=(sig->>case when target='confirmation' then 'confirmed_at' else target end)::bigint;
  select coalesce(jsonb_agg(value order by (value->>'start')::bigint),'[]') into candles from (
   (select r.payload value from dashboard_records r where r.installation=inst and r.kind='candle' and r.payload->>'source'=source and r.payload->>'symbol'=sig->>'symbol' and r.payload->>'timeframe'=sig->>'timeframe' and (r.payload->>case when target='confirmation' then 'end' else 'start' end)::bigint<center order by (r.payload->>'start')::bigint desc limit 100)
   union all
   (select r.payload value from dashboard_records r where r.installation=inst and r.kind='candle' and r.payload->>'source'=source and r.payload->>'symbol'=sig->>'symbol' and r.payload->>'timeframe'=sig->>'timeframe' and (r.payload->>case when target='confirmation' then 'end' else 'start' end)::bigint>=center order by (r.payload->>'start')::bigint limit 101)
  ) v;
  return jsonb_build_object('signal',sig,'candles',candles,'target',target,'target_time',center,'tradingview_url',null,'note','Exact stored source candles. External TradingView mapping is available in the local asset configuration.');
 end if;
 raise exception 'Unknown operation';
end $$;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;
create index if not exists dashboard_candle_lookup on public.dashboard_records(installation,(payload->>'source'),(payload->>'symbol'),(payload->>'timeframe'),((payload->>'start')::bigint)) where kind='candle';

create table if not exists public.dashboard_receipts(installation uuid not null, event_id text not null, primary key(installation,event_id));
alter table public.dashboard_receipts enable row level security;
revoke all on public.dashboard_receipts from anon,authenticated;
alter table public.dashboard_records add column if not exists change_sequence bigint generated always as identity;
create or replace function public.dashboard_ingest(installation uuid, records jsonb, config_base integer default 0)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare rec jsonb; existing jsonb; incoming jsonb; ack jsonb:='[]'; conflicts jsonb:='[]';kind text;k text;
begin
 perform pg_advisory_xact_lock(hashtext(installation::text));
 if not exists(select 1 from dashboard_installations i where i.id=installation) then raise exception 'Unknown installation';end if;
 if jsonb_array_length(records)>100 then raise exception 'Batch too large';end if;
 for rec in select * from jsonb_array_elements(records) loop
  kind:=rec->>'kind';k:=rec->>'key';incoming:=rec->'payload';
  if kind not in ('config','live','backtest','summary','log','candle','backtest_run','feedback','feedback_history','heartbeat') or k is null then raise exception 'Invalid record';end if;
  if exists(select 1 from dashboard_receipts d where d.installation=dashboard_ingest.installation and d.event_id=rec->>'event_id') then ack:=ack||jsonb_build_array(rec->>'event_id');continue;end if;
  select r.payload into existing from dashboard_records r where r.installation=dashboard_ingest.installation and r.kind=kind and r.key=k for update;
  if kind='config' and existing is not null and existing->'config'<>incoming->'config' and (existing->>'revision')::integer<>config_base then conflicts:=conflicts||jsonb_build_array('config');continue;end if;
  if kind='config' and existing is not null and existing->'config'=incoming->'config' then incoming:=existing||jsonb_build_object('applied_revision',incoming->'applied_revision');end if;
  if kind in ('feedback','feedback_history') and existing is not null and (existing->>'revision')::integer>=(incoming->>'revision')::integer then
   if existing->'comment'<>incoming->'comment' or existing->'rating'<>incoming->'rating' then conflicts:=conflicts||jsonb_build_array(k);continue;end if;
   incoming:=existing;
  end if;
  insert into dashboard_records(installation,kind,key,payload) values(installation,kind,k,incoming) on conflict on constraint dashboard_records_pkey do update set payload=excluded.payload,updated_at=now(),change_sequence=default;
  insert into dashboard_receipts values(installation,rec->>'event_id') on conflict do nothing;
  ack:=ack||jsonb_build_array(rec->>'event_id');
 end loop;
 return jsonb_build_object('ack',ack,'conflicts',conflicts,'config',(select r.payload from dashboard_records r where r.installation=dashboard_ingest.installation and r.kind='config' and r.key='current'));
end $$;
revoke all on function public.dashboard_ingest(uuid,jsonb,integer) from public,anon,authenticated;
grant execute on function public.dashboard_ingest(uuid,jsonb,integer) to service_role;
