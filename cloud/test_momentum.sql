-- Scratch database only, after the full schema/migrations and momentum.sql.
insert into auth.users values('00000000-0000-0000-0000-000000000011');
insert into dashboard_installations(id,owner_id) values('00000000-0000-0000-0000-000000000012','00000000-0000-0000-0000-000000000011');
insert into dashboard_records(installation,kind,key,payload) values
 ('00000000-0000-0000-0000-000000000012','summary','momentum-live','{"signals":[],"latest":{"status":"completed"}}'),
 ('00000000-0000-0000-0000-000000000012','summary','momentum-backtest','{"signals":[],"metrics":{"closed_trades":7}}');
set test.auth_uid='00000000-0000-0000-0000-000000000011';
set role authenticated;
do $$begin
 if dashboard_api('00000000-0000-0000-0000-000000000012','/api/momentum?source=live')->'latest'->>'status'<>'completed' then raise exception 'Live summary mismatch';end if;
 if dashboard_api('00000000-0000-0000-0000-000000000012','/api/momentum?source=backtest')->'metrics'->>'closed_trades'<>'7' then raise exception 'Backtest mismatch';end if;
 begin
 perform dashboard_api('00000000-0000-0000-0000-000000000012','/api/momentum?source=invalid');raise exception 'Invalid source accepted';
 exception when raise_exception then if sqlerrm<>'Invalid momentum request' then raise;end if;end;
 begin
 perform dashboard_api_before_momentum('00000000-0000-0000-0000-000000000012','/api/momentum');raise exception 'Internal dispatcher callable';
 exception when insufficient_privilege then null;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000099';
do $$begin
 begin
 perform dashboard_api('00000000-0000-0000-0000-000000000012','/api/momentum?source=live');raise exception 'Unauthorised read accepted';
 exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end $$;
reset role;
