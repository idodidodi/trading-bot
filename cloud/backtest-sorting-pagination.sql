begin;
create or replace function public.dashboard_backtest_sort_value(signal jsonb, review jsonb, field text)
returns jsonb language plpgsql immutable set search_path=public,pg_temp as $$
begin
 if field not in ('asset','timeframe','evidence','confirmed_at','rating') then raise exception 'Invalid finding sort';end if;
 return case field
  when 'asset' then to_jsonb(coalesce(signal->>'symbol',''))
  when 'timeframe' then to_jsonb(case signal->>'timeframe' when '1h' then 60 when '60' then 60 when '4h' then 240 when '240' then 240 when 'daily' then 1440 when 'D' then 1440 when 'weekly' then 10080 when 'W' then 10080 when 'monthly' then 43200 when 'M' then 43200 else 0 end)
  when 'evidence' then to_jsonb(coalesce(signal->>'direction','')||':'||coalesce(signal->>'signal_status','confirmed'))
  when 'confirmed_at' then to_jsonb(coalesce((signal->>'confirmed_at')::bigint,0))
  when 'rating' then review->'rating'
  else null end;
end $$;

do $$
declare target record; definition text; old_order text; new_order text; old_decl text; new_decl text; updated integer:=0;
begin
 old_decl:=$old$declare job dashboard_backtest_jobs; assets jsonb; result jsonb; source text; page integer; path text:=split_part(operation,'?',1);$old$;
 new_decl:=$new$declare job dashboard_backtest_jobs; assets jsonb; result jsonb; source text; page integer; path text:=split_part(operation,'?',1); sortfield text; sortorder text;$new$;
 old_order:=$old$order by (r.payload->'signal'->>'confirmed_at')::bigint desc,r.key limit 51 offset page*50$old$;
 new_order:=$new$order by case when sortfield='rating' and (f.payload->>'rating') is null then 1 else 0 end,
 case when sortorder='asc' then public.dashboard_backtest_sort_value(r.payload->'signal',f.payload,sortfield) end asc,
 case when sortorder='desc' then public.dashboard_backtest_sort_value(r.payload->'signal',f.payload,sortfield) end desc,
 (r.payload->'signal'->>'confirmed_at')::bigint asc,r.key asc limit 51 offset page*50$new$;
 for target in select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname like 'dashboard_api%' loop
  definition:=pg_get_functiondef(target.oid);
  if position('job.finding_ids ? r.key' in definition)>0 then
   if position(old_order in definition)>0 then definition:=replace(definition,old_order,new_order); end if;
   definition:=replace(definition,'sortfield text; sortorder text; sortfield text; sortorder text;','sortfield text; sortorder text;');
   if position(old_decl in definition)>0 and position('sortfield text;' in definition)=0 then definition:=replace(definition,old_decl,new_decl); end if;
   if position('sortfield:=coalesce(substring(operation' in definition)=0 then
    definition:=replace(definition,$anchor$page:=coalesce(substring(operation from 'page=([0-9]+)')::integer,0);if page>10000 then raise exception 'Invalid page';end if;$anchor$,
     $insert$page:=coalesce(substring(operation from 'page=([0-9]+)')::integer,0);if page>10000 then raise exception 'Invalid page';end if;
     sortfield:=coalesce(substring(operation from 'sort=([^&]+)'),'confirmed_at');sortorder:=coalesce(substring(operation from 'order=([^&]+)'),'asc');
     if sortfield not in ('asset','timeframe','evidence','confirmed_at','rating') or sortorder not in ('asc','desc') then raise exception 'Invalid finding sort';end if;$insert$);
   end if;
   definition:=replace(definition,$return$return jsonb_build_object('summary',job.summary,'findings',case when jsonb_array_length(result)>50 then result-50 else result end,'has_more',jsonb_array_length(result)>50);$return$,
    $return$return jsonb_build_object('summary',job.summary,'findings',case when jsonb_array_length(result)>50 then result-50 else result end,'has_more',jsonb_array_length(result)>50,'total_count',jsonb_array_length(job.finding_ids),'page_count',(jsonb_array_length(job.finding_ids)+49)/50);$return$);
   if position('dashboard_backtest_sort_value' in definition)>0 then execute definition;updated:=updated+1;end if;
  end if;
 end loop;
 if updated<>1 then raise exception 'Expected one hosted backtest findings dispatcher; updated %',updated;end if;
end $$;
commit;
