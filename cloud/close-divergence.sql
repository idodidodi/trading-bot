-- Update descriptions without replacing the sharing, signal-action, or follow-up dispatchers.
begin;
do $$
declare target record; definition text; matched integer := 0;
begin
 for target in
  select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname like 'dashboard_api%'
 loop
  definition := pg_get_functiondef(target.oid);
  if position('Consecutive strict pivots: 2 left / 1 right' in definition)>0
     or position('Consecutive strict close-price pivots: 2 left / 1 right' in definition)>0 then
   definition := replace(definition, 'Consecutive strict pivots:', 'Consecutive strict close-price pivots:');
   definition := replace(definition, 'Opposing price and RSI with a band touch', 'Opposing closing price and RSI with a wick band touch');
   execute definition;
   matched := matched + 1;
  end if;
 end loop;
 if matched <> 1 then raise exception 'Expected exactly one backtest strategy dispatcher; found %', matched; end if;
end $$;
commit;
