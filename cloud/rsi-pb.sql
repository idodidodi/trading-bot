-- Additive upgrade after Pin-Bars, CSV, asset strategies and Telegram routing.
-- Supports installations both with and without BB Pin-Bars. Safe to apply twice.
begin;
do $$ begin
 if to_regprocedure('public.dashboard_api_before_rsi_pb(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_rsi_pb;
 end if;
end $$;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare result jsonb; page_text text; page_number integer; items jsonb;
begin
 if split_part(operation,'?',1)='/api/rsi-pb' then
  -- Delegate authorization to the existing sharing/owner chain.
  perform public.dashboard_api_before_rsi_pb(installation,'/api/findings?source=live&page=0','{}'::jsonb);
  page_text:=coalesce(substring(operation from '[?&]page=([^&]+)'),'0');
  if input<>'{}'::jsonb or page_text !~ '^[0-9]{1,5}$' then raise exception 'Invalid RSI + PB request';end if;
  page_number:=page_text::integer;
  if page_number>10000 then raise exception 'Invalid RSI + PB page';end if;
  select r.payload into result from public.dashboard_records r
   where r.installation=installation and r.kind='summary' and r.key='rsi-pb-live';
  result:=coalesce(result,'{"signals":[],"coverage":[],"rules":{"ratio":2,"max_short_wick_ratio":0.66},"scanned_at":null}'::jsonb);
  select coalesce(jsonb_agg(value order by ord),'[]'::jsonb) into items
   from jsonb_array_elements(result->'signals') with ordinality a(value,ord)
   where ord>page_number*50 and ord<=(page_number+1)*50;
  return result || jsonb_build_object('strategy','rsi-pb','signals',items,'has_more',jsonb_array_length(result->'signals')>(page_number+1)*50);
 end if;
 return public.dashboard_api_before_rsi_pb(installation,operation,input);
end $$;
revoke all on function public.dashboard_api_before_rsi_pb(uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function public.dashboard_api(uuid,text,jsonb) to authenticated;

create or replace function public.dashboard_validate_scan_strategies(asset jsonb)
returns void language plpgsql set search_path=public,pg_temp as $$
declare strategies jsonb:=coalesce(asset->'scan_strategies','["rsi","pin-bars","rsi-pb"]'::jsonb);
begin
 if jsonb_typeof(strategies) is distinct from 'array' then raise exception 'Choose at least one supported scan strategy';end if;
 if jsonb_array_length(strategies)=0
    or exists(select 1 from jsonb_array_elements(strategies) s where s not in ('"rsi"'::jsonb,'"pin-bars"'::jsonb,'"bb-pin-bars"'::jsonb,'"rsi-pb"'::jsonb))
    or jsonb_array_length(strategies)<>(select count(distinct s) from jsonb_array_elements(strategies) s) then
  raise exception 'Choose at least one supported scan strategy';
 end if;
end $$;
revoke all on function public.dashboard_validate_scan_strategies(jsonb) from public;

-- Older deployed installations predate independent asset strategy controls.
do $assets$
declare target regprocedure; definition text;
begin
 target:=to_regprocedure('public.dashboard_api_base(uuid,text,jsonb)');
 if target is null then raise exception 'Existing backtests dispatcher required';end if;
 definition:=pg_get_functiondef(target);
 if position('dashboard_validate_scan_strategies' in definition)=0 then
  if position($old$asset:=input->'asset';$old$ in definition)=0
     or position($old$item:=item||jsonb_build_object('provider',asset->'provider'$old$ in definition)=0 then
   raise exception 'Unexpected asset API; no upgrade applied';
  end if;
  definition:=replace(definition,$old$asset:=input->'asset';$old$,$new$asset:=input->'asset'; perform public.dashboard_validate_scan_strategies(asset);$new$);
  definition:=replace(definition,$old$item:=item||jsonb_build_object('provider',asset->'provider'$old$,$new$item:=item||jsonb_build_object('scan_strategies',coalesce(asset->'scan_strategies',item->'scan_strategies','["rsi","pin-bars","rsi-pb"]'::jsonb),'provider',asset->'provider'$new$);
  execute definition;
 end if;
end $assets$;

do $migration$
declare target regprocedure; definition text;
begin
 for target in select p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and (p.proname like 'dashboard_api%' or p.proname='dashboard_save_csv_backtest')
 loop
  definition:=pg_get_functiondef(target);
  definition:=replace(definition,$old$('pin-bars','confirmed','warmup')$old$,$new$('pin-bars','bb-pin-bars','rsi-pb','confirmed','warmup')$new$);
  definition:=replace(definition,$old$('pin-bars','bb-pin-bars','confirmed','warmup')$old$,$new$('pin-bars','bb-pin-bars','rsi-pb','confirmed','warmup')$new$);
  definition:=replace(definition,$old$'other.pin-bars')$old$,$new$'other.pin-bars','forex.rsi-pb','crypto.rsi-pb','stocks.rsi-pb','other.rsi-pb')$new$);
  definition:=replace(definition,$old$'["rsi","pin-bars"]'::jsonb$old$,$new$'["rsi","pin-bars","rsi-pb"]'::jsonb$new$);
  execute definition;
 end loop;
end $migration$;

-- Upgrade authoritative hosted defaults once, retaining revisions and later opt-outs.
do $defaults$
declare row record; config jsonb; assets jsonb; item jsonb; strategies jsonb;
begin
 for row in select installation,payload from public.dashboard_records
  where kind='config' and key='current'
    and coalesce((payload->'config'->>'rsi_pb_defaults_applied')::boolean,false)=false for update
 loop
  config:=row.payload->'config';assets:='[]'::jsonb;
  for item in select value from jsonb_array_elements(config->'assets') loop
   strategies:=coalesce(item->'scan_strategies','["rsi","pin-bars"]'::jsonb);
   if strategies ? 'rsi' and not strategies ? 'rsi-pb' then strategies:=strategies||'"rsi-pb"'::jsonb;end if;
   assets:=assets||jsonb_build_array(item||jsonb_build_object('scan_strategies',strategies));
  end loop;
  config:=config||jsonb_build_object('assets',assets,'rsi_pb_defaults_applied',true);
  update public.dashboard_records r set payload=row.payload||jsonb_build_object('config',config,'revision',(row.payload->>'revision')::bigint+1),
   updated_at=now(),change_sequence=default
   where r.installation=row.installation and r.kind='config' and r.key='current';
 end loop;
end $defaults$;
commit;
