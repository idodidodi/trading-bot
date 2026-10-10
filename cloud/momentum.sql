-- Run after all current migrations. Reuses the existing bounded summary mirror.
-- Idempotent wrapper; retains the installed membership/role authorization logic.
do $$ begin
 if to_regprocedure('public.dashboard_api_before_momentum(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_momentum;
 end if;
end $$;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare source text; result jsonb;
begin
 if split_part(operation,'?',1)='/api/momentum' then
  -- Delegate access checking to the installed dispatcher, including family roles.
  perform public.dashboard_api_before_momentum(installation,'/api/findings?source=live&page=0','{}'::jsonb);
  source:=coalesce(substring(operation from 'source=([^&]+)'),'live');
  if source not in ('live','backtest') or input<>'{}'::jsonb then raise exception 'Invalid momentum request';end if;
  select r.payload into result from public.dashboard_records r
    where r.installation=installation and r.kind='summary' and r.key='momentum-'||source;
  return coalesce(result,jsonb_build_object('signals','[]'::jsonb,'metrics','{}'::jsonb,'note','Awaiting first daily momentum scan and local sync'));
 end if;
 return public.dashboard_api_before_momentum(installation,operation,input);
end $$;
revoke all on function public.dashboard_api_before_momentum(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;
