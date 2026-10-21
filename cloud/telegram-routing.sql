-- Apply after the existing dashboard migrations. No bot credentials are stored.
begin;
create table if not exists public.telegram_routes (
 installation uuid primary key references public.dashboard_installations(id) on delete cascade,
 revision bigint not null default 1 check(revision>0),
 routes jsonb not null default '{}'::jsonb
);
alter table public.telegram_routes enable row level security;
revoke all on public.telegram_routes from public,anon,authenticated;

create or replace function public.telegram_read_routes(installation uuid)
returns jsonb language sql security definer set search_path=public,pg_temp as $$
 select coalesce((select jsonb_build_object('revision',r.revision,'routes',r.routes)
 from public.telegram_routes r where r.installation=$1),'{"revision":0,"routes":{}}'::jsonb)
$$;
revoke all on function public.telegram_read_routes(uuid) from public,anon,authenticated;
grant execute on function public.telegram_read_routes(uuid) to service_role;

do $$ begin
 if to_regprocedure('public.dashboard_api_before_telegram_routing(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_telegram_routing;
 end if;
end $$;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare current_value jsonb; k text; v jsonb;
begin
 if operation='/api/telegram-routing' then
  if not exists(select 1 from public.dashboard_installations i where i.id=installation and i.owner_id=auth.uid()) then
   raise exception 'Only the installation owner can manage Telegram routing';
  end if;
  -- Serialize edits even when no routing row exists yet.
  perform 1 from public.dashboard_installations i where i.id=installation for update;
  current_value:=public.telegram_read_routes(installation);
  if input<>'{}'::jsonb then
   if jsonb_typeof(input)<>'object' or input->>'action' is distinct from 'save'
    or (input-'action'-'revision'-'routes')<>'{}'::jsonb
    or jsonb_typeof(input->'revision') is distinct from 'number'
    or input->'revision'<>current_value->'revision'
    or jsonb_typeof(input->'routes') is distinct from 'object' then
    raise exception 'Invalid routing settings or revision conflict; reload settings';
   end if;
   for k,v in select * from jsonb_each(input->'routes') loop
    if k not in ('general','forex.rsi','forex.pin-bars','crypto.rsi','crypto.pin-bars','stocks.rsi','stocks.pin-bars','other.rsi','other.pin-bars')
     or jsonb_typeof(v)<>'string' or ((v#>>'{}')<>'' and (v#>>'{}') !~ '^-?[1-9][0-9]{0,19}$') then
     raise exception 'Invalid route or chat ID; use numeric strings';
    end if;
   end loop;
   insert into public.telegram_routes as r values(installation,1,input->'routes')
    on conflict on constraint telegram_routes_pkey do update set revision=r.revision+1,routes=excluded.routes;
   current_value:=public.telegram_read_routes(installation);
  end if;
  return current_value||'{"editable":true}'::jsonb;
 end if;
 return public.dashboard_api_before_telegram_routing(installation,operation,input);
end $$;
revoke all on function public.dashboard_api_before_telegram_routing(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;
commit;
