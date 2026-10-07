-- Add Alpaca, Tiingo, OANDA, Kraken, hourly candles and explicit timeframe routing without replacing the backtest dispatcher.
-- Apply after schema.sql (and backtests.sql when installed). Safe to rerun.
begin;
create or replace function public.dashboard_validate_provider_asset(asset jsonb)
returns void language plpgsql set search_path=public,pg_temp as $$
declare overrides jsonb:=coalesce(asset->'timeframe_providers','{}'); frame text; provider text;
begin
 if jsonb_typeof(overrides) is distinct from 'object' then raise exception 'Invalid timeframe providers';end if;
 if exists(select 1 from jsonb_each_text(overrides) o where o.key not in ('1h','4h','daily','weekly','monthly') or o.value not in ('twelvedata','kraken','oanda','tiingo','alpaca') or o.value is null) then raise exception 'Invalid timeframe providers';end if;
 if overrides<>'{}' and asset->>'provider'='csv' then raise exception 'Timeframe overrides require an API feed';end if;
 if asset->>'provider' in ('kraken','oanda','tiingo') and exists(select 1 from jsonb_each_text(overrides) o where o.value='twelvedata') then raise exception 'Use Twelve Data as primary for mixed venues';end if;
 if asset->>'enabled'='true' then
  for frame in select jsonb_array_elements_text(asset->'timeframes') loop
   provider:=coalesce(overrides->>frame,asset->>'provider');
   if provider in ('twelvedata','kraken','oanda','tiingo','alpaca') and (coalesce(asset->>'symbol','')='' or asset->>'feed_confirmed' is distinct from 'true') then raise exception 'Confirm exact feed';end if;
   if provider='alpaca' then
    if coalesce(asset->>'symbol','') !~ '^[A-Z][A-Z0-9.\-]{0,14}$' or asset->>'market' in ('forex','crypto','index','indices','commodity','commodities') or (coalesce(asset->>'market','') not in ('stock','stocks','equity','equities','etf') and coalesce(asset->>'exchange','') not in ('NASDAQ','NYSE','AMEX','NYSE ARCA')) then raise exception 'Alpaca requires a confirmed US equity or ETF feed';end if;
   end if;
   if provider='kraken' then
    if frame='monthly' then raise exception 'Kraken has no calendar-month candles';end if;
    if coalesce(asset->>'symbol','') !~ '^[A-Z0-9]+/[A-Z0-9]+$' then raise exception 'Kraken requires an exact BASE/QUOTE pair';end if;
    if asset->>'provider'='kraken' and asset->>'exchange' is distinct from 'Kraken' then raise exception 'Select Kraken exchange';end if;
   end if;
   if provider='oanda' then
    if coalesce(asset->>'symbol','') !~ '^[A-Z]{3}/[A-Z]{3}$' then raise exception 'OANDA requires an exact forex BASE/QUOTE pair';end if;
    if asset->>'provider'='oanda' and asset->>'exchange' is distinct from 'OANDA' then raise exception 'Select OANDA exchange';end if;
   end if;
   if provider='tiingo' then
    if coalesce(asset->>'symbol','') !~ '^[A-Z]{3}/[A-Z]{3}$' then raise exception 'Tiingo requires an exact forex BASE/QUOTE pair';end if;
    if asset->>'provider'='tiingo' and asset->>'exchange' is distinct from 'Tiingo' then raise exception 'Select Tiingo exchange';end if;
   end if;
  end loop;
 end if;
end $$;
revoke all on function public.dashboard_validate_provider_asset(jsonb) from public;

do $migration$
declare target regprocedure; definition text;
begin
 target:=coalesce(to_regprocedure('public.dashboard_api_base(uuid,text,jsonb)'),to_regprocedure('public.dashboard_api(uuid,text,jsonb)'));
 if target is null then raise exception 'Install schema.sql first';end if;
 definition:=pg_get_functiondef(target);
 if position('dashboard_validate_provider_asset' in definition)=0 then
 if position($old$asset:=input->'asset';$old$ in definition)=0 or position($old$asset->>'provider' not in ('csv','twelvedata')$old$ in definition)=0 then
  raise exception 'Unexpected dashboard API version; provider migration was not applied';
 end if;
 definition:=replace(definition,$old$asset:=input->'asset';$old$,$new$asset:=input->'asset'; perform public.dashboard_validate_provider_asset(asset);$new$);
 definition:=replace(definition,$old$asset->>'provider' not in ('csv','twelvedata')$old$,$new$asset->>'provider' not in ('csv','twelvedata','kraken')$new$);
 definition:=replace(definition,$old$'feed_confirmed',coalesce(asset->'feed_confirmed','false'::jsonb));$old$,$new$'feed_confirmed',coalesce(asset->'feed_confirmed','false'::jsonb),'timeframe_providers',coalesce(asset->'timeframe_providers',item->'timeframe_providers','{}'::jsonb)); perform public.dashboard_validate_provider_asset(item);$new$);
 end if;
 definition:=replace(definition,$old$asset->>'provider' not in ('csv','twelvedata','kraken')$old$,$new$asset->>'provider' not in ('csv','twelvedata','kraken','oanda')$new$);
 definition:=replace(definition,$old$f not in ('monthly','weekly','daily','4h')$old$,$new$f not in ('monthly','weekly','daily','4h','1h')$new$);
 definition:=replace(definition,$old$asset->>'provider' not in ('csv','twelvedata','kraken','oanda')$old$,$new$asset->>'provider' not in ('csv','twelvedata','kraken','oanda','tiingo')$new$);
 definition:=replace(definition,$old$asset->>'provider' not in ('csv','twelvedata','kraken','oanda','tiingo')$old$,$new$asset->>'provider' not in ('csv','twelvedata','kraken','oanda','tiingo','alpaca')$new$);
 execute definition;
end $migration$;
commit;
