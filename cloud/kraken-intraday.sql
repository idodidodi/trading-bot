-- Native Kraken 5m/15m/30m asset configuration. Apply after providers.sql and
-- postgres-dashboard.sql on an existing runtime. Does not enable any feeds.
-- Preserve the current dispatchers, authorization and saved configuration.
begin;
do $migration$
declare target regprocedure; definition text;
begin
 target:='public.dashboard_validate_provider_asset(jsonb)'::regprocedure;
 definition:=pg_get_functiondef(target);
 definition:=replace(definition,
  $old$o.key not in ('1h','4h','daily','weekly','monthly')$old$,
  $new$o.key not in ('5m','15m','30m','1h','4h','daily','weekly','monthly')$new$);
 if position('5m, 15m and 30m candles require Kraken or CSV' in definition)=0 then
  if position($old$provider:=coalesce(overrides->>frame,asset->>'provider');$old$ in definition)=0 then
   raise exception 'Unexpected provider validator; Kraken intraday migration not applied';
  end if;
  definition:=replace(definition,
   $old$provider:=coalesce(overrides->>frame,asset->>'provider');$old$,
   $new$provider:=coalesce(overrides->>frame,asset->>'provider');
   if frame in ('5m','15m','30m') and provider not in ('kraken','csv') then
    raise exception '5m, 15m and 30m candles require Kraken or CSV';
   end if;$new$);
 end if;
 execute definition;
 -- The runtime copies the base asset dispatcher. Upgrade both when present;
 -- fresh installations may run this before the runtime copy is created.
 foreach target in array array[
  to_regprocedure('public.dashboard_api_base(uuid,text,jsonb)'),
  to_regprocedure('bot_runtime.asset_api(uuid,text,jsonb)')
 ] loop
  if target is null then continue;end if;
  definition:=pg_get_functiondef(target);
  if position($old$f not in ('monthly','weekly','daily','4h','1h')$old$ in definition)=0
     and position($new$f not in ('monthly','weekly','daily','4h','1h','30m','15m','5m')$new$ in definition)=0 then
   raise exception 'Unexpected asset dispatcher; Kraken intraday migration not applied';
  end if;
  definition:=replace(definition,
   $old$f not in ('monthly','weekly','daily','4h','1h')$old$,
   $new$f not in ('monthly','weekly','daily','4h','1h','30m','15m','5m')$new$);
  execute definition;
 end loop;
end $migration$;
commit;
