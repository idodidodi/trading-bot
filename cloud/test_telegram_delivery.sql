-- Disposable database only; apply Auth stub, schema, telegram-delivery.sql first.
begin;
insert into auth.users values('10000000-0000-0000-0000-000000000001');
insert into dashboard_installations values('20000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001');
insert into dashboard_installations values('20000000-0000-0000-0000-000000000002','10000000-0000-0000-0000-000000000001');
do $$
declare
 inst uuid:='20000000-0000-0000-0000-000000000001';
 p jsonb:=jsonb_build_object('action','acquire','destination_hash',repeat('a',64),'event_hash',repeat('b',64),'body_hash',repeat('c',64),
  'owner','30000000-0000-0000-0000-000000000001','sender_id','40000000-0000-0000-0000-000000000001','device','home','environment','dev');
 q jsonb; expiry timestamptz;
begin
 assert telegram_coordinate(inst,p)->>'status'='acquired';
 q:=p||jsonb_build_object('owner','30000000-0000-0000-0000-000000000002','device','prod','environment','prod');
 assert telegram_coordinate(inst,q)->>'status'='busy';
 assert telegram_coordinate(inst,q||'{"action":"complete"}')->>'status'='busy';
 assert telegram_coordinate(inst,p||'{"action":"renew"}')->>'status'='acquired';
 update telegram_delivery_claims set lease_until=clock_timestamp()-interval '1 second';
 assert telegram_coordinate(inst,p||'{"action":"renew"}')->>'status'='busy';
 assert telegram_coordinate(inst,q)->>'status'='acquired';
 assert telegram_coordinate(inst,p||'{"action":"release"}')->>'status'='busy';
 assert telegram_coordinate(inst,p||'{"action":"complete"}')->>'status'='busy';
 assert telegram_coordinate(inst,q||'{"action":"complete","message_id":42}')->>'status'='completed';
 select expires_at into expiry from telegram_delivery_claims where installation=inst;
 assert expiry>clock_timestamp()+interval '23 hours 59 minutes';
 assert (select message_id=42 from telegram_delivery_claims where installation=inst);
 -- Different formatting/source must still suppress the same event.
 assert telegram_coordinate(inst,p||jsonb_build_object('body_hash',repeat('d',64)))->>'status'='already_sent';
 assert (select expires_at=expiry from telegram_delivery_claims where installation=inst);
 assert telegram_coordinate(inst,p||jsonb_build_object('destination_hash',repeat('e',64),'owner','30000000-0000-0000-0000-000000000003'))->>'status'='acquired';
 assert telegram_coordinate(inst,p||jsonb_build_object('event_hash',repeat('f',64),'owner','30000000-0000-0000-0000-000000000004'))->>'status'='acquired';
 assert telegram_coordinate('20000000-0000-0000-0000-000000000002',p)->>'status'='acquired';
 -- A claim from yesterday persists across midnight until its rolling expiry.
 update telegram_delivery_claims set expires_at=clock_timestamp()+interval '1 hour' where installation=inst and event_hash=repeat('b',64) and destination_hash=repeat('a',64);
 assert telegram_coordinate(inst,p)->>'status'='already_sent';
 update telegram_delivery_claims set expires_at=clock_timestamp()-interval '1 second' where installation=inst and event_hash=repeat('b',64) and destination_hash=repeat('a',64);
 assert telegram_coordinate(inst,p)->>'status'='acquired';
 assert telegram_coordinate(inst,p||'{"action":"release"}')->>'status'='released';
 assert telegram_coordinate(inst,q)->>'status'='acquired';
 assert (select count(*)>=4 from telegram_delivery_attempts);
 assert not has_function_privilege('authenticated','telegram_coordinate(uuid,jsonb)','execute');
 assert not has_function_privilege('anon','telegram_coordinate(uuid,jsonb)','execute');
 assert has_function_privilege('service_role','telegram_coordinate(uuid,jsonb)','execute');
 assert not has_table_privilege('authenticated','telegram_delivery_attempts','select');
 assert not has_table_privilege('anon','telegram_delivery_claims','select');
 -- Daily maintenance removes expired claims/diagnostics without removing live leases.
 update telegram_delivery_cleanup set last_run='epoch';
 update telegram_delivery_attempts set updated_at=clock_timestamp()-interval '2 days';
 update telegram_delivery_claims set expires_at=clock_timestamp()-interval '1 second' where installation<>inst;
 perform telegram_coordinate(inst,q);
 assert not exists(select 1 from telegram_delivery_claims where installation<>inst);
 assert (select count(*)=1 from telegram_delivery_attempts);
 -- Local acceptance survives a cloud outage longer than the cleanup window.
 delete from telegram_delivery_claims where installation=inst;
 assert telegram_coordinate(inst,q||'{"action":"complete","message_id":43}')->>'status'='completed';
 assert telegram_coordinate(inst,p)->>'status'='already_sent';
end;
$$;
rollback;
