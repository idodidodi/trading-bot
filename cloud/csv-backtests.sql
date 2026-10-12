-- Apply after pin-bars.sql and the existing sharing migrations.
create table if not exists public.dashboard_csv_backtests (
 installation uuid not null references dashboard_installations(id),
 id text not null, report jsonb not null, created timestamptz not null default now(),
 primary key(installation,id)
);
alter table dashboard_csv_backtests enable row level security;
revoke all on dashboard_csv_backtests from public,anon,authenticated;
create or replace function public.dashboard_save_csv_backtest(installation uuid,report jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
begin
 if coalesce(report->>'id','') !~ '^[a-f0-9]{64}$' or jsonb_typeof(report->'signals') is distinct from 'array'
  or coalesce(report->>'strategy','') not in ('pin-bars','confirmed','warmup') then raise exception 'Invalid CSV report';end if;
 insert into dashboard_csv_backtests values(installation,report->>'id',report,now())
 on conflict on constraint dashboard_csv_backtests_pkey do update set report=excluded.report,created=now();
 return jsonb_build_object('saved',true);
end $$;
revoke all on function dashboard_save_csv_backtest(uuid,jsonb) from public,anon,authenticated;
grant execute on function dashboard_save_csv_backtest(uuid,jsonb) to service_role;
do $$begin
 if to_regprocedure('public.dashboard_api_before_csv_backtests(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_csv_backtests;
 end if;
end $$;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare result jsonb; catalog jsonb; items jsonb; page_text text; page_number integer; report_id text; strategy text;
begin
 if split_part(operation,'?',1)='/api/csv-backtests' then
  perform public.dashboard_api_before_csv_backtests(installation,'/api/findings?source=live&page=0','{}'::jsonb);
  if input<>'{}'::jsonb then
   if input<>'{"action":"authorize"}'::jsonb then raise exception 'Invalid CSV operation';end if;
   if not public.dashboard_can_edit(installation) then raise exception 'View-only access';end if;
   return jsonb_build_object('authorized',true);
  end if;
  page_text:=coalesce(substring(operation from 'page=([^&]+)'),'0');
  if page_text !~ '^[0-9]{1,5}$' then raise exception 'Invalid page';end if;
  page_number:=page_text::integer;if page_number>10000 then raise exception 'Invalid page';end if;
  report_id:=substring(operation from '[?&]id=([^&]+)');
  strategy:=substring(operation from 'strategy=([^&]+)');
  if strategy is not null and strategy not in ('pin-bars','confirmed','warmup') then raise exception 'Invalid strategy';end if;
  select r.report into result from dashboard_csv_backtests r where r.installation=installation
   and (report_id is null or r.id=report_id) and (strategy is null or r.report->>'strategy'=strategy)
   order by r.created desc limit 1;
  select coalesce(jsonb_agg(value),'[]'::jsonb) into catalog from (
   select jsonb_build_object('id',r.id,'filename',r.report->'filename','symbol',r.report->'symbol',
    'timeframe',r.report->'timeframe','strategy',r.report->'strategy','start_at',r.report->'start_at',
    'end_at',r.report->'end_at','signal_count',r.report->'signal_count') value
    from dashboard_csv_backtests r where r.installation=installation order by r.created desc limit 50
  ) q;
  if result is not null then
   select coalesce(jsonb_agg(value order by ord),'[]'::jsonb) into items
    from jsonb_array_elements(result->'signals') with ordinality a(value,ord)
    where ord>page_number*50 and ord<=(page_number+1)*50;
   result:=result||jsonb_build_object('signals',items,'has_more',jsonb_array_length(result->'signals')>(page_number+1)*50);
  end if;
  return jsonb_build_object('report',result,'reports',catalog);
 end if;
 return public.dashboard_api_before_csv_backtests(installation,operation,input);
end $$;
revoke all on function dashboard_api_before_csv_backtests(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function dashboard_api(uuid,text,jsonb) to authenticated;
