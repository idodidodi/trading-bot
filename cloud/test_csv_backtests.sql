-- Scratch database only, after sharing, Pin-Bars and CSV migrations.
insert into auth.users values('00000000-0000-0000-0000-000000000031'),('00000000-0000-0000-0000-000000000033');
insert into dashboard_installations values('00000000-0000-0000-0000-000000000032','00000000-0000-0000-0000-000000000031');
insert into dashboard_members(installation,user_id,role) values('00000000-0000-0000-0000-000000000032','00000000-0000-0000-0000-000000000033','viewer');
set role service_role;
select dashboard_save_csv_backtest('00000000-0000-0000-0000-000000000032',jsonb_build_object('id',repeat('a',64),'strategy','pin-bars','signals','[]'::jsonb,'start_at',1000,'end_at',2000));
reset role;
set test.auth_uid='00000000-0000-0000-0000-000000000031';
set role authenticated;
do $$begin
 if dashboard_api('00000000-0000-0000-0000-000000000032','/api/csv-backtests')->'report'->>'end_at'<>'2000' then raise exception 'Saved date range missing';end if;
 if dashboard_api('00000000-0000-0000-0000-000000000032','/api/csv-backtests','{"action":"authorize"}')->>'authorized'<>'true' then raise exception 'Owner upload denied';end if;
 begin
  perform dashboard_save_csv_backtest('00000000-0000-0000-0000-000000000032','{}');raise exception 'Browser evidence write allowed';
 exception when insufficient_privilege then null;end;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000032','/api/csv-backtests?page=-1');raise exception 'Invalid page allowed';
 exception when raise_exception then if sqlerrm<>'Invalid page' then raise;end if;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000033';
do $$begin
 if dashboard_api('00000000-0000-0000-0000-000000000032','/api/csv-backtests')->'report' is null then raise exception 'Viewer read denied';end if;
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000032','/api/csv-backtests','{"action":"authorize"}');raise exception 'Viewer upload allowed';
 exception when raise_exception then if sqlerrm<>'View-only access' then raise;end if;end;
end $$;
set test.auth_uid='00000000-0000-0000-0000-000000000099';
do $$begin
 begin
  perform dashboard_api('00000000-0000-0000-0000-000000000032','/api/csv-backtests');raise exception 'Isolation failed';
 exception when raise_exception then if sqlerrm<>'Not authorized' then raise;end if;end;
end $$;
reset role;
