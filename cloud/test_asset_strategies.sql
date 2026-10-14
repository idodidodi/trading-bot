-- Scratch database only: auth stub, schema, test_api, backtests, providers, asset-strategies first.
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$
declare cfg jsonb; revision integer; candidate jsonb; saved jsonb; choices jsonb; bad jsonb;
begin
 cfg:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets');
 revision:=(cfg->>'revision')::integer;
 candidate:='{"id":"NVDA","provider":"csv","enabled":true,"timeframes":["4h"]}';
 foreach choices in array array['["rsi"]'::jsonb,'["pin-bars"]'::jsonb,'["rsi","pin-bars"]'::jsonb] loop
  saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||jsonb_build_object('scan_strategies',choices)));
  if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'id'='NVDA' and a->'scan_strategies'=choices) then raise exception 'Scan strategies were not saved';end if;
  revision:=(saved->>'revision')::integer;
 end loop;
 foreach bad in array array['[]'::jsonb,'["unknown"]'::jsonb,'["rsi","rsi"]'::jsonb,'"rsi"'::jsonb,'null'::jsonb,'[{}]'::jsonb] loop
  begin
   perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||jsonb_build_object('scan_strategies',bad)));
   raise exception 'Invalid scan strategies accepted';
  exception when raise_exception then if sqlerrm='Invalid scan strategies accepted' then raise;end if;end;
 end loop;
 -- Older clients preserve a selection made by a newer editor.
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate||'{"scan_strategies":["pin-bars"]}'::jsonb));
 revision:=(saved->>'revision')::integer;
 saved:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets',jsonb_build_object('revision',revision,'action','save','asset',candidate));
 if not exists(select 1 from jsonb_array_elements(saved->'config'->'assets') a where a->>'id'='NVDA' and a->'scan_strategies'='["pin-bars"]'::jsonb) then raise exception 'Legacy save reset strategies';end if;
end $$;
reset role;
