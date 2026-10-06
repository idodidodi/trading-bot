-- Run after test_api.sql in the disposable test database.
insert into dashboard_records values('00000000-0000-0000-0000-000000000002','candle','hist','{"source":"backtest","symbol":"NVDA","timeframe":"daily","start":1000,"end":2000,"open":1,"high":2,"low":1,"close":1}',now(),default);
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
do $$declare options jsonb; result jsonb;begin
 options:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests','{}');
 if not options->'assets' ? 'NVDA' then raise exception 'Historical catalogue missing';end if;
 result:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests','{"assets":["NVDA"],"timeframes":["daily"],"strategy":"confirmed"}');
 if result->>'status'<>'running' then raise exception 'Job did not start';end if;
 result:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests','{"assets":["NVDA"],"timeframes":["daily"],"strategy":"confirmed"}');
 if not result ? 'error' then raise exception 'Concurrent job allowed';end if;
 begin perform dashboard_finish_backtest('00000000-0000-0000-0000-000000000002',gen_random_uuid(),'[]','[]');raise exception 'Browser completion allowed';exception when insufficient_privilege then null;end;
end $$;
reset role;
do $$declare job uuid;result jsonb;begin
 select id into job from dashboard_backtest_jobs where status='running';
 perform dashboard_finish_backtest('00000000-0000-0000-0000-000000000002',job,'[{"asset":"NVDA","timeframe":"daily","status":"replayed","signals":1}]','[{"id":"cloud-finding","signal":{"symbol":"NVDA","timeframe":"daily","direction":"bullish","pivot1":1000,"pivot2":2000,"confirmed_at":3000,"price1":2,"price2":1,"rsi1":10,"rsi2":20,"band1":3,"band2":2,"rules":{}}}]');
 result:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/findings?source=backtest&page=0','{}');
 if jsonb_array_length(result->'findings')<>1 or result->'summary'->0->>'status'<>'replayed' then raise exception 'Completed report unavailable';end if;
end $$;
set role authenticated;
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/feedback','{"source":"backtest","finding_id":"cloud-finding","revision":0,"rating":4,"comment":"Cloud replay review"}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests','{"assets":["NVDA"],"timeframes":["daily"],"strategy":"confirmed"}');
reset role;
do $$declare job uuid;result jsonb;begin
 select id into job from dashboard_backtest_jobs where status='running';
 perform dashboard_finish_backtest('00000000-0000-0000-0000-000000000002',job,'[]','[{"id":"another-id","signal":{"symbol":"NVDA","timeframe":"daily","direction":"bullish","pivot1":1000,"pivot2":2000,"confirmed_at":3000,"price1":2,"price2":1,"rsi1":10,"rsi2":20,"band1":3,"band2":2,"rules":{}}}]');
 result:=dashboard_api('00000000-0000-0000-0000-000000000002','/api/findings?source=backtest&page=0','{}');
 if result->'findings'->0->>'id'<>'cloud-finding' or result->'findings'->0->'feedback'->>'rating'<>'4' then raise exception 'Replay lost evidence feedback';end if;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000003';
set role authenticated;
do $$begin
 begin perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/backtests','{}');raise exception 'Owner isolation failed';exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end$$;
reset role;
