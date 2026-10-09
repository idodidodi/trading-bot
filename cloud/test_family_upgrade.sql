begin;
insert into auth.users values('00000000-0000-0000-0000-000000000091');
insert into dashboard_installations values('00000000-0000-0000-0000-000000000092','00000000-0000-0000-0000-000000000091');
select set_config('test.auth_uid','00000000-0000-0000-0000-000000000091',true);
insert into dashboard_records(installation,kind,key,payload) values
 ('00000000-0000-0000-0000-000000000092','config','current','{"revision":1,"applied_revision":1,"config":{"assets":[]}}'),
 ('00000000-0000-0000-0000-000000000092','summary','selections','{"weekly_stock_selection":{"status":"selected","symbols":["A","B"]}}'),
 ('00000000-0000-0000-0000-000000000092','summary','live','[{"asset":"NASA","status":"insufficient history"}]');
do $$
declare inst uuid:='00000000-0000-0000-0000-000000000092'; first jsonb; second jsonb; assets jsonb;
begin
 first:=dashboard_api(inst,'/api/catchup','{"action":"request"}');
 second:=dashboard_api(inst,'/api/catchup','{"action":"request"}');
 if first->>'id' is distinct from second->>'id' then raise exception 'Repeated click queued duplicate commands';end if;
 if jsonb_array_length(dashboard_worker_commands(inst))<>1 then raise exception 'Missing worker command';end if;
 assets:=dashboard_api(inst,'/api/assets');
 if assets->'weekly_stock_selection'->>'status' is distinct from 'selected' or jsonb_array_length(assets->'coverage')<>1 then raise exception 'Selection/coverage not visible online';end if;
 insert into dashboard_records(installation,kind,key,payload) values(inst,'summary','catchup:'||(first->>'id'),'{"status":"completed","findings":3}');
 if jsonb_array_length(dashboard_worker_commands(inst))<>0 then raise exception 'Completed request still delivered';end if;
 if dashboard_api(inst,'/api/catchup')->>'status' is distinct from 'completed' then raise exception 'Completion not shown';end if;
 if not (dashboard_api(inst,'/api/backtests')->'timeframes' ? '1h') then raise exception 'Hourly replay option missing';end if;
end $$;
set local role authenticated;
select dashboard_api('00000000-0000-0000-0000-000000000092','/api/assets');
select set_config('test.auth_uid','00000000-0000-0000-0000-000000000093',true);
do $$begin
 begin perform dashboard_api('00000000-0000-0000-0000-000000000092','/api/catchup','{"action":"request"}');raise exception 'Unauthorized access allowed';
 exception when raise_exception then if SQLERRM<>'Not authorized' then raise;end if;end;
end $$;
rollback;
