-- Scratch PostgreSQL only: run test_auth_stub.sql, schema.sql, then this file.
insert into auth.users values('00000000-0000-0000-0000-000000000001');
insert into dashboard_installations values('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001');
select dashboard_ingest('00000000-0000-0000-0000-000000000002','[{"kind":"config","key":"current","event_id":"initial-config","payload":{"revision":1,"applied_revision":1,"config":{"timeframes":["monthly","weekly","daily","4h"],"assets":[{"id":"NVDA","provider":"csv","enabled":true,"timeframes":["daily"],"path":"data/candles/{asset}-{timeframe}.csv","tradingview_symbol":"NASDAQ:NVDA"}]}}}]',0);
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets','{}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets','{"action":"bulk","revision":1,"tickers":"MSFT, msft, BTCUSD"}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/findings?source=live&page=0','{}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/logs','{}');
reset role;
select dashboard_ingest('00000000-0000-0000-0000-000000000002','[{"kind":"live","key":"finding","event_id":"signal-1","payload":{"id":"finding","signal":{"symbol":"NVDA","timeframe":"daily","confirmed_at":1582588800000,"pivot1":1581984000000,"pivot2":1582329600000,"price1":100,"price2":90,"rsi1":15,"rsi2":20}}},{"kind":"candle","key":"bar1","event_id":"bar1","payload":{"source":"live","symbol":"NVDA","timeframe":"daily","start":1582329600000,"end":1582416000000,"open":100,"high":101,"low":90,"close":95}}]',1);
set test.auth_uid='00000000-0000-0000-0000-000000000001';
set role authenticated;
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets','{"action":"save","revision":2,"asset":{"id":"NVDA","provider":"csv","enabled":true,"timeframes":["monthly","4h"],"tradingview_symbol":"NASDAQ:NVDA"}}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/feedback','{"source":"live","finding_id":"finding","revision":0,"rating":5,"comment":"Good setup"}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/findings?source=live&page=0','{}');
select dashboard_api('00000000-0000-0000-0000-000000000002','/api/candles?source=live&finding=finding&target=pivot2','{}');
do $$begin
 begin insert into dashboard_records(installation,kind,key,payload) values('00000000-0000-0000-0000-000000000002','candle','forbidden','{}');raise exception 'Browser write unexpectedly succeeded';exception when insufficient_privilege then null;end;
end$$;
set test.auth_uid='00000000-0000-0000-0000-000000000003';
do $$begin
 begin perform dashboard_api('00000000-0000-0000-0000-000000000002','/api/assets','{}');raise exception 'Owner isolation failed';exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end$$;
reset role;
