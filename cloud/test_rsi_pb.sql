-- Disposable database only, after rsi-pb.sql applied twice.
insert into auth.users values('00000000-0000-0000-0000-000000000051'),('00000000-0000-0000-0000-000000000053');
insert into dashboard_installations(id,owner_id) values('00000000-0000-0000-0000-000000000052','00000000-0000-0000-0000-000000000051');
insert into dashboard_records(installation,kind,key,payload) values('00000000-0000-0000-0000-000000000052','config','current',
 '{"revision":8,"applied_revision":8,"config":{"timeframes":["daily"],"assets":[{"id":"A","provider":"csv","enabled":false,"timeframes":["daily"],"scan_strategies":["rsi"]},{"id":"PINS","scan_strategies":["pin-bars"]}]}}');
\ir rsi-pb.sql
do $$declare cfg jsonb;begin
 select payload into cfg from dashboard_records where installation='00000000-0000-0000-0000-000000000052' and kind='config' and key='current';
 if cfg->>'revision'<>'9' or cfg->'config'->'assets'->0->'scan_strategies'<>'["rsi","rsi-pb"]'::jsonb
    or cfg->'config'->'assets'->1->'scan_strategies'<>'["pin-bars"]'::jsonb then raise exception 'Default upgrade mismatch';end if;
 update dashboard_records set payload=jsonb_set(payload,'{config,assets,0,scan_strategies}','["rsi"]') where installation='00000000-0000-0000-0000-000000000052' and kind='config' and key='current';
end $$;
\ir rsi-pb.sql
do $$declare cfg jsonb;begin
 select payload into cfg from dashboard_records where installation='00000000-0000-0000-0000-000000000052' and kind='config' and key='current';
 if cfg->>'revision'<>'9' or cfg->'config'->'assets'->0->'scan_strategies'<>'["rsi"]'::jsonb then raise exception 'Opt-out overwritten';end if;
end $$;
insert into dashboard_records(installation,kind,key,payload)
select '00000000-0000-0000-0000-000000000052','summary','rsi-pb-live',
 jsonb_build_object('signals',jsonb_agg(jsonb_build_object('candle_at',n) order by n),'coverage','[]'::jsonb)
 from generate_series(1,51) n;
set test.auth_uid='00000000-0000-0000-0000-000000000051';
set role authenticated;
do $$declare result jsonb;begin
 result:=dashboard_api('00000000-0000-0000-0000-000000000052','/api/rsi-pb?page=0');
 if jsonb_array_length(result->'signals')<>50 or not (result->>'has_more')::boolean then raise exception 'First page mismatch';end if;
 result:=dashboard_api('00000000-0000-0000-0000-000000000052','/api/rsi-pb?page=1');
 if jsonb_array_length(result->'signals')<>1 or (result->>'has_more')::boolean then raise exception 'Second page mismatch';end if;
 if dashboard_api('00000000-0000-0000-0000-000000000052','/api/pin-bars')->'signals'<>'[]'::jsonb then raise exception 'Other strategy leaked';end if;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000052','/api/rsi-pb?page=-1');raise exception 'Invalid page accepted';
 exception when raise_exception then if sqlerrm<>'Invalid RSI + PB request' then raise;end if;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000052','/api/rsi-pb','{"write":true}'::jsonb);raise exception 'Write accepted';
 exception when raise_exception then if sqlerrm<>'Invalid RSI + PB request' then raise;end if;end;
 begin
  perform dashboard_api_before_rsi_pb('00000000-0000-0000-0000-000000000052','/api/rsi-pb');raise exception 'Internal dispatcher callable';
 exception when insufficient_privilege then null;end;
 result:=dashboard_api('00000000-0000-0000-0000-000000000052','/api/telegram-routing','{"action":"save","revision":0,"routes":{"general":"-101","forex.rsi-pb":"-102","crypto.rsi-pb":"-103","stocks.rsi-pb":"-104","other.rsi-pb":"-105"}}');
 if result->'routes'->>'stocks.rsi-pb'<>'-104' then raise exception 'Route settings failed';end if;
end $$;
reset role;
select dashboard_validate_scan_strategies('{"scan_strategies":["rsi-pb"]}'::jsonb);
select dashboard_validate_scan_strategies('{"scan_strategies":["rsi","pin-bars","bb-pin-bars","rsi-pb"]}'::jsonb);
set role service_role;
select dashboard_save_csv_backtest('00000000-0000-0000-0000-000000000052',jsonb_build_object('id',repeat('c',64),'strategy','rsi-pb','signals','[]'::jsonb));
reset role;
set test.auth_uid='00000000-0000-0000-0000-000000000051';
set role authenticated;
do $$begin
 if dashboard_api('00000000-0000-0000-0000-000000000052','/api/csv-backtests?strategy=rsi-pb')->'report'->>'strategy'<>'rsi-pb' then raise exception 'CSV read failed';end if;
end $$;
reset role;
insert into dashboard_members(installation,user_id,role) values('00000000-0000-0000-0000-000000000052','00000000-0000-0000-0000-000000000053','viewer');
set test.auth_uid='00000000-0000-0000-0000-000000000053';
set role authenticated;
do $$begin
 perform dashboard_api('00000000-0000-0000-0000-000000000052','/api/rsi-pb');
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000052','/api/telegram-routing');raise exception 'Viewer routing access';
 exception when raise_exception then if sqlerrm='Viewer routing access' then raise;end if;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000052','/api/csv-backtests','{"action":"authorize"}');raise exception 'Viewer upload access';
 exception when raise_exception then if sqlerrm='Viewer upload access' then raise;end if;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000099';
do $$begin
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000052','/api/rsi-pb');raise exception 'Unauthorized read accepted';
 exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end $$;
reset role;
