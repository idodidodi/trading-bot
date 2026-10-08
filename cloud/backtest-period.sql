-- Keep replay options aligned with the currently published historical report.
begin;
do $$
declare target record; definition text;
begin
 for target in select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname like 'dashboard_api%' loop
  definition:=pg_get_functiondef(target.oid);
  if position($old$'year',2020,'strategies'$old$ in definition)>0 then
   definition:=replace(definition,$old$'year',2020,'strategies'$old$,
    $new$'year',coalesce((select (r.payload->>'year')::integer from dashboard_records r where r.installation=installation and r.kind='backtest_run' order by r.updated_at desc limit 1),2020),'strategies'$new$);
   execute definition;
  end if;
 end loop;
end $$;
commit;
