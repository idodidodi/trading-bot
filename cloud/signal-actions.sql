-- Run after schema.sql, backtests.sql, providers.sql and sharing.sql.
-- Keep visibility separate from evidence: scanner snapshots cannot resurrect deletions.
begin;
do $$ begin
 if to_regprocedure('public.dashboard_api_before_signal_actions(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_signal_actions;
 end if;
end $$;
revoke all on function public.dashboard_api_before_signal_actions(uuid,text,jsonb) from public,anon,authenticated;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare
 path text:=split_part(operation,'?',1); q text:=split_part(operation,'?',2);
 source text; key text; action text; state jsonb; expected integer; page integer; result jsonb;
begin
 if not public.dashboard_can_view(installation) then raise exception 'Not authorized';end if;
 if path='/api/finding-state' then
  if not public.dashboard_can_edit(installation) then raise exception 'View-only access';end if;
  source:=input->>'source';key:=input->>'finding_id';action:=input->>'action';
  if source is distinct from 'live' or key is null or coalesce(action,'') not in ('archive','restore','delete')
   or coalesce(input->>'revision','') !~ '^[0-9]+$' then raise exception 'Invalid signal action';end if;
  -- Serialize with ingestion, as well as edits made in other sessions.
  perform pg_advisory_xact_lock(hashtext(installation::text));
  if not exists(select 1 from dashboard_records r where r.installation=installation and r.kind='live' and r.key=key) then raise exception 'Signal not found';end if;
  select r.payload into state from dashboard_records r where r.installation=installation and r.kind='finding_state' and r.key=key;
  expected:=coalesce((state->>'revision')::integer,0);
  if (input->>'revision')::integer<>expected then return jsonb_build_object('error','Signal changed in another session. Reload before saving.');end if;
  if state->>'status'='deleted' then raise exception 'Signal has been deleted';end if;
  state:=jsonb_build_object('finding_id',key,'status',case action when 'archive' then 'archived' when 'restore' then 'active' else 'deleted' end,'revision',expected+1,'updated_at',extract(epoch from now()));
  insert into dashboard_records(installation,kind,key,payload) values(installation,'finding_state',key,state)
   on conflict on constraint dashboard_records_pkey do update set payload=excluded.payload,updated_at=now(),change_sequence=default;
  return state||jsonb_build_object('saved',true);
 elsif path='/api/findings' and coalesce(substring(q from 'source=([^&]+)'),'live')='live' then
  page:=coalesce(substring(q from 'page=([^&]+)')::integer,0);
  if page<0 or page>10000 then raise exception 'Invalid page';end if;
  select coalesce(jsonb_agg(value),'[]') into result from (
   select r.payload||jsonb_build_object('feedback',f.payload,'archived',coalesce(s.payload->>'status','active')='archived','state_revision',coalesce((s.payload->>'revision')::integer,0)) value
   from dashboard_records r
   left join dashboard_records f on f.installation=r.installation and f.kind='feedback' and f.key=r.key
   left join dashboard_records s on s.installation=r.installation and s.kind='finding_state' and s.key=r.key
   where r.installation=installation and r.kind='live' and coalesce(s.payload->>'status','active')<>'deleted'
   and (substring(q from 'archived=([^&]+)')='true' or coalesce(s.payload->>'status','active')<>'archived')
   order by (r.payload->'signal'->>'confirmed_at')::bigint desc,r.key limit 51 offset page*50
  ) v;
  return jsonb_build_object('findings',case when jsonb_array_length(result)>50 then result-50 else result end,'has_more',jsonb_array_length(result)>50,
   'summary',coalesce((select r.payload from dashboard_records r where r.installation=installation and r.kind='summary' and r.key='live'),'[]'));
 end if;
 return public.dashboard_api_before_signal_actions(installation,operation,input);
end $$;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;

-- Extend the existing bounded sync RPC without replacing provider/config behavior.
do $$
declare definition text;
begin
 definition:=pg_get_functiondef('public.dashboard_ingest(uuid,jsonb,integer)'::regprocedure);
 if position('Invalid finding state' in definition)=0 then
  if position($old$'feedback_history','heartbeat'$old$ in definition)=0 then raise exception 'Unexpected ingest allowlist';end if;
  definition:=replace(definition,$old$'feedback_history','heartbeat'$old$,$new$'feedback_history','heartbeat','finding_state'$new$);
  definition:=replace(definition,$old$if kind='config' and existing is not null and existing->'config'<>incoming->'config'$old$,$new$
  if kind='finding_state' then
   if incoming->>'finding_id' is distinct from k or coalesce(incoming->>'status','') not in ('active','archived','deleted')
    or coalesce(incoming->>'revision','') !~ '^[1-9][0-9]*$' then raise exception 'Invalid finding state';end if;
   if existing is not null and ((existing->>'revision')::integer>=(incoming->>'revision')::integer or existing->>'status'='deleted') then
    incoming:=existing;
   end if;
  end if;
  if kind='config' and existing is not null and existing->'config'<>incoming->'config'$new$);
  execute definition;
 end if;
end $$;
commit;
