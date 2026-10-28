-- Scratch database only: auth stub, schema, test_api, backtests, providers first.
do $$
declare candidate jsonb;
begin
 -- Cash index names must not be silently accepted as futures or another venue.
 foreach candidate in array array[
  '{"id":"SPX","provider":"yahoo","exchange":"SNP","symbol":"^GSPC","market":"index","enabled":true,"feed_confirmed":true,"timeframes":["1h","4h","monthly"]}'::jsonb,
  '{"id":"DOLLAR_INDEX","provider":"yahoo","exchange":"NYB","symbol":"DX-Y.NYB","market":"index","enabled":true,"feed_confirmed":true,"timeframes":["1h","4h","monthly"]}'::jsonb
 ] loop
  perform dashboard_validate_provider_asset(candidate);
  begin
   perform dashboard_validate_provider_asset(candidate||'{"exchange":"XETRA"}'::jsonb);
   raise exception 'Wrong Yahoo index venue accepted';
  exception when raise_exception then if sqlerrm='Wrong Yahoo index venue accepted' then raise;end if;end;
 end loop;
end $$;
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
 -- Market classification must persist for promoted stock feeds without a listed-exchange mapping.
 candidate:=candidate||'{"exchange":"Alpaca-sip-split","market":"stock","timeframes":["weekly","daily"]}'::jsonb;
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'id'='BTCUSD' and a->>'market'='stock') then raise exception 'Stock market classification was not saved';end if;
 revision:=(saved->>'revision')::integer;
 candidate:=candidate||'{"enabled":false,"timeframes":["1h"]}'::jsonb;
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'id'='BTCUSD' and a->>'enabled'='false' and a->'timeframes'='["1h"]') then raise exception 'Stock scanner settings were not saved';end if;
 revision:=(saved->>'revision')::integer;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||'{"market":"unknown"}'::jsonb));
  raise exception 'Unknown market accepted';
 exception when raise_exception then if sqlerrm='Unknown market accepted' then raise;end if;end;
 -- The existing dispatcher must still handle backtest options.
 perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests');
end $$;
reset role;
