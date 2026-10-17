-- Update installed descriptions without replacing newer dashboard API wrappers.
begin;
do $$
declare target record; definition text; matched integer := 0;
begin
 for target in
  select p.oid from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname like 'dashboard_api%'
 loop
  definition := pg_get_functiondef(target.oid);
  if position('Consecutive strict close-price pivots: 2 left / 1 right; spacing 5–60.' in definition)>0 then
   definition := replace(definition,
    'Consecutive strict close-price pivots: 2 left / 1 right; spacing 5–60. Opposing closing price and RSI with a wick band touch at P2;',
    'Strict close-price pivots: 2 left / 1 right; compare all earlier same-type pivots at spacing 2–60 (at least one intervening candle). Only P2 must wick-touch its band; opposing closing price and RSI; no slope filter;');
   execute definition;
   matched := matched + 1;
  elsif position('compare all earlier same-type pivots at spacing 2–60' in definition)>0 then
   matched := matched + 1;
  end if;
 end loop;
 if matched <> 1 then raise exception 'Expected exactly one backtest strategy dispatcher; found %', matched; end if;
end $$;
commit;
