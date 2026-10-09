-- Add durable hosted catch-up commands, selection snapshots and optional hourly replay.
begin;
do $$
declare target record; definition text;
begin
 for target in select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname like 'dashboard_api%' loop
  definition:=pg_get_functiondef(target.oid);
  if position('declare job dashboard_backtest_jobs' in definition)>0 then
   definition:=replace(definition,$old$jsonb_array_length(input->'timeframes') not between 1 and 4$old$,$new$jsonb_array_length(input->'timeframes') not between 1 and 5$new$);
   definition:=replace(definition,$old$t not in ('monthly','weekly','daily','4h')$old$,$new$t not in ('monthly','weekly','daily','4h','1h')$new$);
   definition:=replace(definition,$old$'timeframes',jsonb_build_array('monthly','weekly','daily','4h')$old$,$new$'timeframes',jsonb_build_array('monthly','weekly','daily','4h','1h')$new$);
   execute definition;
  end if;
 end loop;
 if to_regprocedure('public.dashboard_api_before_family(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_family;
 end if;
end $$;
revoke all on function public.dashboard_api_before_family(uuid,text,jsonb) from public,anon,authenticated;

create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare path text:=split_part(operation,'?',1); result jsonb; request_id text;
begin
 if not public.dashboard_can_view(installation) then raise exception 'Not authorized';end if;
 if path='/api/catchup' and input<>'{}' and not public.dashboard_can_edit(installation) then raise exception 'View-only access';end if;
 if path='/api/catchup' then
  if input<>'{}' then
   if input->>'action' is distinct from 'request' then raise exception 'Invalid scanner action';end if;
   -- Serialize clicks from separate browsers; one unfinished request per installation.
   perform 1 from dashboard_installations i where i.id=installation for update;
   select r.key into request_id from dashboard_records r where r.installation=installation and r.kind='command'
    and not exists(select 1 from dashboard_records s where s.installation=installation and s.kind='summary'
      and s.key='catchup:'||r.key and s.payload->>'status' in ('completed','failed')) order by r.updated_at limit 1;
   if request_id is null then
    request_id:=gen_random_uuid()::text;
    insert into dashboard_records(installation,kind,key,payload) values(installation,'command',request_id,jsonb_build_object('id',request_id,'action','catchup','status','pending','created',extract(epoch from now())));
   end if;
   return coalesce((select s.payload from dashboard_records s where s.installation=installation and s.kind='summary' and s.key='catchup:'||request_id),jsonb_build_object('id',request_id,'status','pending'));
  end if;
  select r.key into request_id from dashboard_records r where r.installation=installation and r.kind='command' order by r.updated_at desc limit 1;
  return coalesce((select s.payload from dashboard_records s where s.installation=installation and s.kind='summary' and s.key='catchup:'||request_id),jsonb_build_object('id',request_id,'status',case when request_id is null then 'idle' else 'pending' end));
 end if;
 result:=public.dashboard_api_before_family(installation,operation,input);
 if path='/api/assets' then
  result:=result||coalesce((select r.payload from dashboard_records r where r.installation=installation and r.kind='summary' and r.key='selections'),'{}')
   ||jsonb_build_object('coverage',coalesce((select r.payload from dashboard_records r where r.installation=installation and r.kind='summary' and r.key='live'),'[]'));
 elsif path='/api/findings' and coalesce(substring(operation from 'source=([^&]+)'),'live')='live' then
  result:=result||jsonb_build_object('scan',(select r.payload from dashboard_records r where r.installation=installation and r.kind='summary' and r.key='scanner'));
 end if;
 return result;
end $$;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;

create or replace function public.dashboard_worker_commands(installation uuid)
returns jsonb language sql security definer set search_path=public,pg_temp as $$
 select coalesce(jsonb_agg(value),'[]') from (select r.payload value from dashboard_records r
 where r.installation=installation and r.kind='command' and r.payload->>'action'='catchup'
 and not exists(select 1 from dashboard_records s where s.installation=installation and s.kind='summary'
   and s.key='catchup:'||r.key and s.payload->>'status' in ('completed','failed'))
 order by r.updated_at limit 20) pending;
$$;
revoke all on function public.dashboard_worker_commands(uuid) from public,anon,authenticated;
grant execute on function public.dashboard_worker_commands(uuid) to service_role;
commit;
