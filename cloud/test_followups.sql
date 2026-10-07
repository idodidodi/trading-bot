-- Disposable PostgreSQL after all dashboard migrations, including followups.sql.
insert into dashboard_records(installation,kind,key,payload) values
 ('00000000-0000-0000-0000-000000000002','live','followup-test','{"id":"followup-test","signal":{"symbol":"TEST","timeframe":"1h","direction":"bullish","pivot2":1577836800000,"price2":100,"confirmed_at":1577836860000}}');
insert into dashboard_records(installation,kind,key,payload)
select '00000000-0000-0000-0000-000000000002','candle','followup-bar-'||i,
 jsonb_build_object('source','live','symbol','TEST','timeframe','1h','start',1577836800000+i*60000::bigint,'end',1577836800000+(i+1)*60000::bigint,
 'open',100,'high',case when i=104 then 140 when i=103 then 120 else 110 end,'low',case when i=102 then 80 when i=103 then 90 else 100 end,'close',case when i=102 then 90 else 100 end)
from generate_series(0,104) i;
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$
declare inst uuid:='00000000-0000-0000-0000-000000000002';result jsonb;f jsonb;
begin
 result:=dashboard_api(inst,'/api/findings?source=live');
 select v->'followup' into f from jsonb_array_elements(result->'findings') v where v->>'id'='followup-test';
 if f is distinct from 'null'::jsonb then raise exception 'Live followup calculated before click';end if;
 perform dashboard_api(inst,'/api/feedback','{"source":"live","finding_id":"followup-test","rating":5,"comment":"Keep my review","revision":0}');
 result:=dashboard_api(inst,'/api/followup?source=live&finding=followup-test');f:=result->'followup';
 if (f->>'drawdown_pct')::numeric<>20 or (f->>'gain_pct')::numeric<>20 or (f->>'candles')::integer<>103 or f->>'rating'<>'3'
  or (f->>'recovery_elapsed_ms')::bigint<>104*60000 then raise exception 'Recovery metrics wrong: %',f;end if;
 result:=dashboard_api(inst,'/api/followup?source=live&finding=followup-test&mode=all');
 if (result->'followup'->>'gain_pct')::numeric<>40 or result->'followup'->>'rating'<>'4' then raise exception 'All history metrics wrong';end if;
 result:=dashboard_api(inst,'/api/findings?source=live');
 select v into f from jsonb_array_elements(result->'findings') v where v->>'id'='followup-test';
 if f->'followup'->>'rating'<>'3' or f->'feedback'->>'rating'<>'5' or f->'feedback'->>'comment'<>'Keep my review' then raise exception 'Saved metrics or feedback lost';end if;
 begin
  perform dashboard_api(inst,'/api/followup?source=live&finding=missing');raise exception 'Missing finding accepted';
 exception when raise_exception then if sqlerrm<>'Finding not found' then raise;end if;end;
 begin
  insert into dashboard_followups values(inst,'arbitrary','{}',now());raise exception 'Browser wrote metrics';
 exception when insufficient_privilege then null;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000003';
do $$begin
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/followup?source=live&finding=followup-test');raise exception 'Cross-owner read accepted';
 exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end $$;
reset role;
