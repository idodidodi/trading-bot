-- Disposable PostgreSQL only, after test_api.sql and the additive migrations.
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$
declare inst uuid:='00000000-0000-0000-0000-000000000002'; result jsonb;
begin
 result:=dashboard_api(inst,'/api/finding-state','{"source":"live","finding_id":"finding","action":"archive","revision":0}');
 if result->>'status'<>'archived' then raise exception 'Archive failed';end if;
 result:=dashboard_api(inst,'/api/findings?source=live');
 if jsonb_array_length(result->'findings')<>0 then raise exception 'Archive visible by default';end if;
 result:=dashboard_api(inst,'/api/findings?source=live&archived=true');
 if jsonb_array_length(result->'findings')<>1 or result->'findings'->0->>'archived'<>'true' then raise exception 'Show archived failed';end if;
 result:=dashboard_api(inst,'/api/finding-state','{"source":"live","finding_id":"finding","action":"restore","revision":0}');
 if not result ? 'error' then raise exception 'Stale edit accepted';end if;
 perform dashboard_api(inst,'/api/finding-state','{"source":"live","finding_id":"finding","action":"restore","revision":1}');
 result:=dashboard_api(inst,'/api/findings?source=live');
 if jsonb_array_length(result->'findings')<>1 then raise exception 'Restore failed';end if;
 perform dashboard_api(inst,'/api/finding-state','{"source":"live","finding_id":"finding","action":"delete","revision":2}');
 result:=dashboard_api(inst,'/api/findings?source=live&archived=true');
 if jsonb_array_length(result->'findings')<>0 then raise exception 'Delete failed';end if;
 begin
  perform dashboard_api(inst,'/api/finding-state','{"source":"live","finding_id":"finding","action":"restore","revision":3}');
  raise exception 'Deleted signal restored';
 exception when raise_exception then if sqlerrm<>'Signal has been deleted' then raise;end if;end;
end $$;
reset role;
-- A later scanner upload and an offline restore cannot resurrect a deletion.
select dashboard_ingest('00000000-0000-0000-0000-000000000002','[{"kind":"finding_state","key":"finding","event_id":"offline-restore","payload":{"finding_id":"finding","status":"active","revision":4,"updated_at":1}},{"kind":"live","key":"finding","event_id":"rescan","payload":{"id":"finding","signal":{"symbol":"NVDA","confirmed_at":1}}}]',0);
do $$begin
 if (select payload->>'status' from dashboard_records where kind='finding_state' and key='finding')<>'deleted' then raise exception 'Sync resurrected deletion';end if;
end $$;
insert into auth.users values('00000000-0000-0000-0000-000000000004');
insert into dashboard_members(installation,user_id,role) values('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000004','viewer');
set test.auth_uid='00000000-0000-0000-0000-000000000004';
set role authenticated;
do $$begin
 begin
 perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/finding-state','{"source":"live","finding_id":"finding","action":"archive","revision":3}');
 raise exception 'Viewer modified signal';
 exception when raise_exception then if sqlerrm<>'View-only access' then raise;end if;end;
end $$;
reset role;
