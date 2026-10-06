do $$begin if not exists(select 1 from pg_roles where rolname='anon') then create role anon;end if;end$$;
do $$begin if not exists(select 1 from pg_roles where rolname='authenticated') then create role authenticated;end if;end$$;
do $$begin if not exists(select 1 from pg_roles where rolname='service_role') then create role service_role;end if;end$$;
create schema auth;
create table auth.users(id uuid primary key);
create function auth.uid() returns uuid language sql as $$select nullif(current_setting('test.auth_uid',true),'')::uuid$$;
grant usage on schema auth to authenticated;
grant execute on function auth.uid() to authenticated;
