-- Add multi-user dashboard sharing to an existing Family Trading Bot installation.
-- Run after schema.sql and backtests.sql. Invite users through Supabase Auth first,
-- then add their Auth user ID to dashboard_members as an editor.
begin;
create table if not exists public.dashboard_members (
 installation uuid not null references public.dashboard_installations(id) on delete cascade,
 user_id uuid not null references auth.users(id) on delete cascade,
 role text not null default 'viewer' check (role in ('viewer','editor')),
 created_at timestamptz not null default now(),
 primary key (installation,user_id)
);
alter table public.dashboard_members enable row level security;
revoke all on public.dashboard_members from public,anon,authenticated;

create or replace function public.dashboard_can_view(inst uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
 select exists(select 1 from public.dashboard_installations i where i.id=inst and i.owner_id=auth.uid())
     or exists(select 1 from public.dashboard_members m where m.installation=inst and m.user_id=auth.uid());
$$;
create or replace function public.dashboard_can_edit(inst uuid)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
 select exists(select 1 from public.dashboard_installations i where i.id=inst and i.owner_id=auth.uid())
     or exists(select 1 from public.dashboard_members m where m.installation=inst and m.user_id=auth.uid() and m.role='editor');
$$;
revoke all on function public.dashboard_can_view(uuid),public.dashboard_can_edit(uuid) from public,anon;
grant execute on function public.dashboard_can_view(uuid),public.dashboard_can_edit(uuid) to authenticated;

drop policy if exists owner_installation_read on public.dashboard_installations;
create policy owner_installation_read on public.dashboard_installations for select to authenticated
 using (public.dashboard_can_view(id));
drop policy if exists owner_record_read on public.dashboard_records;
create policy owner_record_read on public.dashboard_records for select to authenticated
 using (public.dashboard_can_view(installation));

-- Existing deployed functions each contain one explicit owner-only guard. Keep
-- their validated behavior and route that guard through the membership check.
do $$
declare
 target regprocedure;
 definition text;
 old_guard text;
 new_guard text;
 replaced boolean;
begin
 old_guard := $old$if not exists(select 1 from dashboard_installations i where i.id=inst and i.owner_id=auth.uid()) then raise exception 'Not authorized';end if;$old$;
 new_guard := $new$if not public.dashboard_can_view(inst) then raise exception 'Not authorized';end if;
 if not public.dashboard_can_edit(inst) and (path='/api/feedback' or (path='/api/assets' and input<>'{}'::jsonb)) then raise exception 'View-only access';end if;$new$;
 target := to_regprocedure('public.dashboard_api_base(uuid,text,jsonb)');
 if target is null then raise exception 'dashboard_api_base is missing; install the current dashboard schema first';end if;
 definition := pg_get_functiondef(target);
 replaced := position(old_guard in definition)>0;
 if replaced then
  definition := replace(definition,old_guard,new_guard);
 elsif position('public.dashboard_can_view(inst)' in definition)=0 then
  raise exception 'Expected owner guard not found in dashboard_api_base';
 end if;
 execute definition;

 target := to_regprocedure('public.dashboard_api(uuid,text,jsonb)');
 if target is null then raise exception 'dashboard_api dispatcher is missing';end if;
 definition := pg_get_functiondef(target);
 old_guard := $old$if not exists(select 1 from dashboard_installations i where i.id=installation and i.owner_id=auth.uid()) then raise exception 'Not authorized';end if;$old$;
 new_guard := $new$if not public.dashboard_can_view(installation) then raise exception 'Not authorized';end if;
 if path='/api/backtests' and input<>'{}'::jsonb and not public.dashboard_can_edit(installation) then raise exception 'View-only access';end if;$new$;
 replaced := position(old_guard in definition)>0;
 if replaced then
  definition := replace(definition,old_guard,new_guard);
 elsif position('public.dashboard_can_view(installation)' in definition)=0 then
  raise exception 'Expected owner guard not found in dashboard_api dispatcher';
 end if;
 execute definition;
end $$;
commit;
