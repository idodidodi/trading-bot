-- Scratch database only: test_auth_stub.sql, schema.sql, telegram-routing.sql, this file.
insert into auth.users values('00000000-0000-0000-0000-000000000001'),('00000000-0000-0000-0000-000000000003');
insert into dashboard_installations values('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001');
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$ declare value jsonb; begin
 value:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing');
 if value->>'revision'<>'0' then raise exception 'Incorrect initial revision';end if;
 value:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing','{"action":"save","revision":0,"routes":{"general":"-100123","forex.rsi":"-100456"}}');
 if value->>'revision'<>'1' or value->'routes'->>'general'<>'-100123' then raise exception 'Settings were not saved';end if;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing','{"action":"save","revision":0,"routes":{}}');
  raise exception 'Stale edit accepted';
 exception when raise_exception then if sqlerrm='Stale edit accepted' then raise;end if;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing','{"action":"save","revision":1,"routes":{"general":123}}');
  raise exception 'Numeric JSON ID accepted';
 exception when raise_exception then if sqlerrm='Numeric JSON ID accepted' then raise;end if;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing','{"revision":1,"routes":{}}');
  raise exception 'Missing action accepted';
 exception when raise_exception then if sqlerrm='Missing action accepted' then raise;end if;end;
 begin
  perform telegram_read_routes('00000000-0000-0000-0000-000000000002');raise exception 'Machine RPC exposed';
 exception when insufficient_privilege then null;end;
 begin
  perform 1 from telegram_routes;raise exception 'Direct browser table access allowed';
 exception when insufficient_privilege then null;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000003';
do $$ begin
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing');raise exception 'Other user read allowed';
 exception when raise_exception then if sqlerrm='Other user read allowed' then raise;end if;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing','{"action":"save","revision":1,"routes":{}}');raise exception 'Other user write allowed';
 exception when raise_exception then if sqlerrm='Other user write allowed' then raise;end if;end;
end $$;
reset role;
set role service_role;
do $$ declare value jsonb;begin
 value:=telegram_read_routes('00000000-0000-0000-0000-000000000002');
 if value->>'revision'<>'1' then raise exception 'Machine read failed';end if;
end $$;
reset role;
