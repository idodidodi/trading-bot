-- Run after signal-actions.sql. Derived outcomes never modify signal evidence or feedback.
begin;
create table if not exists public.dashboard_followups (
 installation uuid not null references dashboard_installations(id), finding_id text not null,
 results jsonb not null, calculated_at timestamptz not null default now(),
 primary key(installation,finding_id)
);
alter table dashboard_followups enable row level security;
revoke all on dashboard_followups from public,anon,authenticated;
create index if not exists dashboard_candle_followup on dashboard_records
 (installation,(payload->>'source'),(payload->>'symbol'),(payload->>'timeframe'),((payload->>'start')::bigint)) where kind='candle';

create or replace function public.dashboard_calculate_followup(sig jsonb,bars jsonb,mode text)
returns jsonb language plpgsql set search_path=public,pg_temp as $$
declare
 start_at bigint:=(sig->>'pivot2')::bigint; price numeric:=(sig->>'price2')::numeric;
 bullish boolean:=sig->>'direction'='bullish'; result jsonb; c jsonb;
 dd numeric:=0; gain numeric:=0; adverse numeric; favorable numeric; unfavorable_price numeric; favorable_price numeric;
 dd_at bigint; gain_at bigint; dd_price numeric; gain_price numeric; recovery_at bigint; adverse_at bigint; through_at bigint;
 count integer:=0; ratio numeric; rating integer;
begin
 if mode not in ('recovery','all') then raise exception 'Invalid follow-up window';end if;
 result:=jsonb_build_object('version',1,'mode',mode,'baseline_at',start_at,'baseline_price',price,'status','unavailable','rating',null,'candles',0);
 if price<=0 or not exists(select 1 from jsonb_array_elements(bars) b where (b->>'start')::bigint=start_at) then
  return result||jsonb_build_object('reason','The original pivot candle is missing from stored history.');
 end if;
 for c in select b from jsonb_array_elements(bars) b where (b->>'start')::bigint>start_at order by (b->>'start')::bigint loop
  count:=count+1; through_at:=(c->>'end')::bigint;
  unfavorable_price:=(c->>case when bullish then 'low' else 'high' end)::numeric;
  favorable_price:=(c->>case when bullish then 'high' else 'low' end)::numeric;
  adverse:=greatest(0,(case when bullish then price-unfavorable_price else unfavorable_price-price end)/price*100);
  favorable:=greatest(0,(case when bullish then favorable_price-price else price-favorable_price end)/price*100);
  if adverse>dd then dd:=adverse;dd_at:=through_at;dd_price:=unfavorable_price;recovery_at:=null;end if;
  if favorable>gain then gain:=favorable;gain_at:=through_at;gain_price:=favorable_price;end if;
  if dd_at is not null and dd_at<through_at and recovery_at is null and (case when bullish then (c->>'close')::numeric>=price else (c->>'close')::numeric<=price end) then recovery_at:=through_at;end if;
  if adverse>0 and adverse_at is null then adverse_at:=through_at;end if;
  if recovery_at is not null and mode='recovery' then exit;end if;
 end loop;
 if count=0 then return result||jsonb_build_object('reason','No closed candles after the divergence pivot yet.');end if;
 ratio:=case when dd>0 then gain/dd else null end;
 rating:=case when gain=0 then 1 when dd=0 or ratio>=3 then 5 when ratio>=2 then 4 when ratio>=1 then 3 when ratio>=.5 then 2 else 1 end;
 return result||jsonb_build_object('status',case when recovery_at is not null then 'recovered' when adverse_at is not null then 'ongoing' else 'no drawdown' end,
  'candles',count,'through_at',through_at,'drawdown_pct',dd,'drawdown_price',dd_price,'drawdown_at',dd_at,'drawdown_elapsed_ms',dd_at-start_at,
  'gain_pct',gain,'gain_price',gain_price,'gain_at',gain_at,'gain_elapsed_ms',gain_at-start_at,'recovery_at',recovery_at,
  'recovery_elapsed_ms',recovery_at-start_at,'gain_drawdown_ratio',ratio,'rating',rating);
end $$;
revoke all on function dashboard_calculate_followup(jsonb,jsonb,text) from public,anon,authenticated;

do $$ begin
 if to_regprocedure('public.dashboard_api_before_followups(uuid,text,jsonb)') is null then
  alter function public.dashboard_api(uuid,text,jsonb) rename to dashboard_api_before_followups;
 end if;
end $$;
revoke all on function dashboard_api_before_followups(uuid,text,jsonb) from public,anon,authenticated;
create or replace function public.dashboard_api(installation uuid,operation text,input jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare
 path text:=split_part(operation,'?',1);q text:=split_part(operation,'?',2);source text;key text;mode text;
 sig jsonb;bars jsonb;windows jsonb;result jsonb;items jsonb;item jsonb;cutoff bigint;
begin
 if not public.dashboard_can_view(installation) then raise exception 'Not authorized';end if;
 if path='/api/followup' then
  source:=substring(q from 'source=([^&]+)');key:=substring(q from 'finding=([^&]+)');mode:=coalesce(substring(q from 'mode=([^&]+)'),'recovery');
  if coalesce(source,'') not in ('live','backtest') or mode not in ('recovery','all') then raise exception 'Invalid follow-up';end if;
  select r.payload->'signal',r.payload->'followups' into sig,windows from dashboard_records r where r.installation=installation and r.kind=source and r.key=key;
  if sig is null or exists(select 1 from dashboard_records r where r.installation=installation and r.kind='finding_state' and r.key=key and r.payload->>'status'='deleted') then raise exception 'Finding not found';end if;
  if source='backtest' and windows->mode is not null then return jsonb_build_object('signal',sig,'followup',windows->mode);end if;
  cutoff:=case when source='backtest' then 1609459200000 else (extract(epoch from now())*1000)::bigint end;
  select coalesce(jsonb_agg(r.payload order by (r.payload->>'start')::bigint),'[]') into bars from dashboard_records r
   where r.installation=installation and r.kind='candle' and r.payload->>'source'=source
   and r.payload->>'symbol'=sig->>'symbol' and r.payload->>'timeframe'=sig->>'timeframe'
   and (r.payload->>'start')::bigint>=(sig->>'pivot2')::bigint and (r.payload->>'end')::bigint<=cutoff;
  windows:=jsonb_build_object('recovery',dashboard_calculate_followup(sig,bars,'recovery'),'all',dashboard_calculate_followup(sig,bars,'all'));
  insert into dashboard_followups(installation,finding_id,results) values(installation,key,windows)
   on conflict on constraint dashboard_followups_pkey do update set results=excluded.results,calculated_at=now();
  return jsonb_build_object('signal',sig,'followup',windows->mode);
 end if;
 result:=public.dashboard_api_before_followups(installation,operation,input);
 if path='/api/findings' then
  items:='[]';
  for item in select * from jsonb_array_elements(result->'findings') loop
   select f.results into windows from dashboard_followups f where f.installation=installation and f.finding_id=item->>'id';
   windows:=coalesce(windows,item->'followups');
   -- Backfill older replay rows when reviewed, using the same stored series.
   if substring(q from 'source=([^&]+)')='backtest' and coalesce(windows->'recovery'->>'status','')='' then
    sig:=item->'signal';
    select coalesce(jsonb_agg(r.payload order by (r.payload->>'start')::bigint),'[]') into bars from dashboard_records r
     where r.installation=installation and r.kind='candle' and r.payload->>'source'='backtest'
     and r.payload->>'symbol'=sig->>'symbol' and r.payload->>'timeframe'=sig->>'timeframe'
     and (r.payload->>'start')::bigint>=(sig->>'pivot2')::bigint and (r.payload->>'end')::bigint<=1609459200000;
    windows:=jsonb_build_object('recovery',dashboard_calculate_followup(sig,bars,'recovery'),'all',dashboard_calculate_followup(sig,bars,'all'));
    insert into dashboard_followups(installation,finding_id,results) values(installation,item->>'id',windows)
     on conflict on constraint dashboard_followups_pkey do update set results=excluded.results,calculated_at=now();
   end if;
   items:=items||jsonb_build_array(item||jsonb_build_object('followup',windows->'recovery'));
  end loop;
  result:=jsonb_set(result,'{findings}',items);
 end if;
 return result;
end $$;
revoke all on function dashboard_api(uuid,text,jsonb) from public,anon;
grant execute on function dashboard_api(uuid,text,jsonb) to authenticated;
-- A replay matching existing evidence must still refresh the derived outcomes.
do $$
declare definition text;
begin
 definition:=pg_get_functiondef('public.dashboard_finish_backtest(uuid,uuid,jsonb,jsonb)'::regprocedure);
 if position('Refresh derived followups' in definition)=0 then
  definition:=replace(definition,'ids:=ids||jsonb_build_array(existing);', $new$
  -- Refresh derived followups without replacing evidence or user feedback.
  update dashboard_records r set payload=r.payload||jsonb_build_object('followups',item->'followups'),updated_at=now(),change_sequence=default
   where r.installation=installation and r.kind='backtest' and r.key=existing;
  ids:=ids||jsonb_build_array(existing);$new$);
  execute definition;
 end if;
end $$;
commit;
