-- Scratch database only, after BB Pin-Bars migration (applied twice).
insert into auth.users values('00000000-0000-0000-0000-000000000041');
insert into dashboard_installations(id,owner_id) values('00000000-0000-0000-0000-000000000042','00000000-0000-0000-0000-000000000041');
insert into dashboard_records(installation,kind,key,payload)
select '00000000-0000-0000-0000-000000000042','summary','bb-pin-bars-live',
 jsonb_build_object('signals',jsonb_agg(jsonb_build_object('candle_at',n) order by n),'coverage','[]'::jsonb)
 from generate_series(1,51) n;
set test.auth_uid='00000000-0000-0000-0000-000000000041';
set role authenticated;
do $$declare result jsonb;begin
 result:=dashboard_api('00000000-0000-0000-0000-000000000042','/api/bb-pin-bars?page=0');
 if jsonb_array_length(result->'signals')<>50 or not (result->>'has_more')::boolean then raise exception 'First page mismatch';end if;
 result:=dashboard_api('00000000-0000-0000-0000-000000000042','/api/bb-pin-bars?page=1');
 if jsonb_array_length(result->'signals')<>1 or (result->>'has_more')::boolean or result->'signals'->0->>'candle_at'<>'51' then raise exception 'Second page mismatch';end if;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000042','/api/bb-pin-bars?page=-1');
  raise exception 'Invalid page accepted';
 exception when raise_exception then if sqlerrm<>'Invalid BB Pin-Bars request' then raise;end if;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000042','/api/bb-pin-bars','{"write":true}'::jsonb);
  raise exception 'Write accepted';
 exception when raise_exception then if sqlerrm<>'Invalid BB Pin-Bars request' then raise;end if;end;
 begin
  perform dashboard_api_before_bb_pin_bars('00000000-0000-0000-0000-000000000042','/api/bb-pin-bars');
  raise exception 'Internal dispatcher callable';
 exception when insufficient_privilege then null;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000099';
do $$begin
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000042','/api/bb-pin-bars');
  raise exception 'Unauthorized read accepted';
 exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end $$;
reset role;

select dashboard_validate_scan_strategies('{"scan_strategies":["bb-pin-bars"]}'::jsonb);
select dashboard_validate_scan_strategies('{"scan_strategies":["rsi","pin-bars","bb-pin-bars"]}'::jsonb);
set role service_role;
select dashboard_save_csv_backtest('00000000-0000-0000-0000-000000000042',jsonb_build_object('id',repeat('b',64),'strategy','bb-pin-bars','signals','[]'::jsonb));
reset role;
set test.auth_uid='00000000-0000-0000-0000-000000000041';
set role authenticated;
do $$begin
 if dashboard_api('00000000-0000-0000-0000-000000000042','/api/csv-backtests?strategy=bb-pin-bars')->'report'->>'strategy'<>'bb-pin-bars' then raise exception 'BB CSV read failed';end if;
 if dashboard_api('00000000-0000-0000-0000-000000000042','/api/csv-backtests?strategy=pin-bars')->'report'<>'null'::jsonb then raise exception 'Strategies leaked';end if;
end $$;
reset role;
