-- Apply after the existing dashboard migrations, including momentum.sql.
do $$ begin
 if to_regprocedure('public.dashboard_api_before_pin_bars(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_pin_bars;
 end if;
end $$;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare result jsonb; page_text text; page_number integer; items jsonb;
begin
 if split_part(operation,'?',1)='/api/pin-bars' then
  perform public.dashboard_api_before_pin_bars(installation,'/api/findings?source=live&page=0','{}'::jsonb);
  page_text:=coalesce(substring(operation from 'page=([^&]+)'),'0');
  if input<>'{}'::jsonb or page_text !~ '^[0-9]{1,5}$' then raise exception 'Invalid Pin-Bars request';end if;
  page_number:=page_text::integer;
  if page_number>10000 then raise exception 'Invalid Pin-Bars page';end if;
  select r.payload into result from public.dashboard_records r
   where r.installation=installation and r.kind='summary' and r.key='pin-bars-live';
  result:=coalesce(result,'{"signals":[],"coverage":[],"rules":{"fast":8,"slow":21,"ratio":2},"scanned_at":null}'::jsonb);
  select coalesce(jsonb_agg(value order by ord),'[]'::jsonb) into items
   from jsonb_array_elements(result->'signals') with ordinality a(value,ord)
   where ord>page_number*50 and ord<=(page_number+1)*50;
  return result || jsonb_build_object('signals',items,'has_more',jsonb_array_length(result->'signals')>(page_number+1)*50);
 end if;
 return public.dashboard_api_before_pin_bars(installation,operation,input);
end $$;
revoke all on function public.dashboard_api_before_pin_bars(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;
