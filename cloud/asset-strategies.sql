-- Apply after the existing schema/provider migrations. Preserves API dispatchers.
begin;
create or replace function public.dashboard_validate_scan_strategies(asset jsonb)
returns void language plpgsql set search_path=public,pg_temp as $$
declare strategies jsonb:=coalesce(asset->'scan_strategies','["rsi","pin-bars"]'::jsonb);
begin
 if jsonb_typeof(strategies) is distinct from 'array' then
  raise exception 'Choose RSI divergence, Pin-Bars, or both scan strategies';
 end if;
 if jsonb_array_length(strategies)=0
    or exists(select 1 from jsonb_array_elements(strategies) s where s not in ('"rsi"'::jsonb,'"pin-bars"'::jsonb))
    or jsonb_array_length(strategies)<>(select count(distinct s) from jsonb_array_elements(strategies) s) then
  raise exception 'Choose RSI divergence, Pin-Bars, or both scan strategies';
 end if;
end $$;
revoke all on function public.dashboard_validate_scan_strategies(jsonb) from public;

do $migration$
declare target regprocedure; definition text;
begin
 target:=coalesce(to_regprocedure('public.dashboard_api_base(uuid,text,jsonb)'),to_regprocedure('public.dashboard_api(uuid,text,jsonb)'));
 if target is null then raise exception 'Install schema.sql first';end if;
 definition:=pg_get_functiondef(target);
 if position('dashboard_validate_scan_strategies' in definition)=0 then
  if position($old$asset:=input->'asset';$old$ in definition)=0
     or position($old$item:=item||jsonb_build_object('provider',asset->'provider'$old$ in definition)=0 then
   raise exception 'Unexpected dashboard API version; strategy migration was not applied';
  end if;
  definition:=replace(definition,$old$asset:=input->'asset';$old$,$new$asset:=input->'asset'; perform public.dashboard_validate_scan_strategies(asset);$new$);
  definition:=replace(definition,$old$item:=item||jsonb_build_object('provider',asset->'provider'$old$,$new$item:=item||jsonb_build_object('scan_strategies',coalesce(asset->'scan_strategies',item->'scan_strategies','["rsi","pin-bars"]'::jsonb),'provider',asset->'provider'$new$);
  execute definition;
 end if;
end $migration$;
commit;
