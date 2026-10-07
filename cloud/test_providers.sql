-- Scratch database only: auth stub, schema, test_api, backtests, providers first.
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$
declare cfg jsonb; revision integer; candidate jsonb; saved jsonb; bad jsonb;
begin
 cfg:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets');
 revision:=(cfg->>'revision')::integer;
 candidate:='{"id":"BTCUSD","provider":"twelvedata","exchange":"Binance","symbol":"BTC/USD","feed_confirmed":true,"enabled":true,"timeframes":["monthly","weekly","4h"],"timeframe_providers":{"weekly":"kraken","4h":"kraken"}}';
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'id'='BTCUSD' and a->'timeframe_providers'='{"weekly":"kraken","4h":"kraken"}') then raise exception 'Provider routing was not saved';end if;
 revision:=(saved->>'revision')::integer;
 foreach bad in array array[candidate||'{"timeframe_providers":{"monthly":"kraken"}}'::jsonb,candidate||'{"timeframe_providers":{"4h":"unknown"}}'::jsonb,candidate||'{"feed_confirmed":false}'::jsonb] loop
  begin
   perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',bad));
   raise exception 'Invalid provider configuration accepted';
  exception when raise_exception then if sqlerrm='Invalid provider configuration accepted' then raise;end if;end;
 end loop;
 candidate:=candidate||'{"provider":"kraken","exchange":"Kraken","timeframes":["4h"],"timeframe_providers":{}}'::jsonb;
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'id'='BTCUSD' and a->>'provider'='kraken') then raise exception 'Kraken primary not saved';end if;
 revision:=(saved->>'revision')::integer;
 candidate:='{"id":"BTCUSD","provider":"oanda","exchange":"OANDA","symbol":"EUR/USD","feed_confirmed":true,"enabled":true,"timeframes":["1h","monthly"],"timeframe_providers":{}}';
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'provider'='oanda' and a->'timeframes' ? '1h') then raise exception 'OANDA hourly routing not saved';end if;
 revision:=(saved->>'revision')::integer;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||'{"symbol":"DAX"}'::jsonb));
  raise exception 'Invalid OANDA instrument accepted';
 exception when raise_exception then if sqlerrm='Invalid OANDA instrument accepted' then raise;end if;end;
 candidate:=candidate||'{"provider":"tiingo","exchange":"Tiingo"}'::jsonb;
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'provider'='tiingo' and a->'timeframes' ? '1h') then raise exception 'Tiingo hourly routing not saved';end if;
 revision:=(saved->>'revision')::integer;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||'{"symbol":"DAX"}'::jsonb));
  raise exception 'Invalid Tiingo instrument accepted';
 exception when raise_exception then if sqlerrm='Invalid Tiingo instrument accepted' then raise;end if;end;
 candidate:=candidate||'{"provider":"alpaca","exchange":"NASDAQ","symbol":"DAX","timeframes":["1h","monthly"],"market":"etf"}'::jsonb;
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'provider'='alpaca' and a->'timeframes' ? '1h') then raise exception 'Alpaca hourly routing not saved';end if;
 revision:=(saved->>'revision')::integer;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||'{"market":"index"}'::jsonb));
  raise exception 'Cash index accepted as Alpaca equity';
 exception when raise_exception then if sqlerrm='Cash index accepted as Alpaca equity' then raise;end if;end;
 -- The existing dispatcher must still handle backtest options.
 perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests');
end $$;
reset role;
