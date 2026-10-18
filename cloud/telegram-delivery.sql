-- Additive, machine-only delivery coordination. Safe to reapply.
create table if not exists public.telegram_delivery_claims(
 installation uuid not null references public.dashboard_installations(id),
 destination_hash text not null, event_hash text not null, body_hash text not null,
 owner uuid not null, sender_id uuid not null, device text not null, environment text not null,
 state text not null check(state in ('sending','sent')), lease_until timestamptz not null,
 expires_at timestamptz not null, message_id bigint,
 primary key(installation,destination_hash,event_hash)
);
create table if not exists public.telegram_delivery_attempts(
 installation uuid not null references public.dashboard_installations(id),
 owner uuid not null, destination_hash text not null,event_hash text not null,body_hash text not null,
 sender_id uuid not null,device text not null,environment text not null,
 outcome text not null, reason text, message_id bigint, updated_at timestamptz not null,
 primary key(installation,owner)
);
create table if not exists public.telegram_delivery_cleanup(singleton boolean primary key default true check(singleton), last_run timestamptz not null);
insert into public.telegram_delivery_cleanup values(true,'epoch') on conflict do nothing;
alter table public.telegram_delivery_claims enable row level security;
alter table public.telegram_delivery_attempts enable row level security;
alter table public.telegram_delivery_cleanup enable row level security;
revoke all on public.telegram_delivery_claims,public.telegram_delivery_attempts,public.telegram_delivery_cleanup from public,anon,authenticated;
create index if not exists telegram_claim_expiry on public.telegram_delivery_claims(expires_at);
create index if not exists telegram_attempt_age on public.telegram_delivery_attempts(updated_at);

create or replace function public.telegram_coordinate(installation uuid,input jsonb)
returns jsonb language plpgsql security definer set search_path=public,pg_temp as $$
#variable_conflict use_variable
declare
 inst uuid:=installation; action text:=input->>'action'; dest text:=input->>'destination_hash'; event text:=input->>'event_hash';
 body text:=input->>'body_hash'; token uuid:=(input->>'owner')::uuid; sender uuid:=(input->>'sender_id')::uuid;
 stamp timestamptz:=clock_timestamp(); claim public.telegram_delivery_claims%rowtype;
 result text; why text;
begin
 if inst is null or token is null or sender is null or action is null or action not in ('acquire','renew','complete','release')
  or dest is null or dest !~ '^[a-f0-9]{64}$' or event is null or event !~ '^[a-f0-9]{64}$'
  or body is null or body !~ '^[a-f0-9]{64}$' or coalesce(input->>'environment','') not in ('dev','prod')
  or coalesce(length(input->>'device'),0) not between 1 and 80 then raise exception 'Invalid coordination request';end if;
 -- Lazy daily maintenance: first authenticated operation of the day cleans up.
 update telegram_delivery_cleanup set last_run=stamp where singleton and last_run<=stamp-interval '1 day';
 if found then
  delete from telegram_delivery_claims where expires_at<=stamp;
  delete from telegram_delivery_attempts where updated_at<=stamp-interval '1 day';
 end if;
 perform pg_advisory_xact_lock(hashtextextended(inst::text||dest||event,0));
 stamp:=clock_timestamp();
 select * into claim from telegram_delivery_claims c where c.installation=inst and c.destination_hash=dest and c.event_hash=event for update;
 if found and claim.state='sent' and (claim.expires_at>stamp or action='complete') then
  result:='already_sent';why:='Successful delivery retained for 24 hours';
 elsif action='acquire' then
  if claim.owner is not null and claim.state='sending' and claim.lease_until>stamp and claim.owner<>token then
   result:='busy';why:='Another sender holds the lease';
  else
   insert into telegram_delivery_claims values(inst,dest,event,body,token,sender,input->>'device',input->>'environment','sending',stamp+interval '90 seconds',stamp+interval '1 day',null)
   on conflict on constraint telegram_delivery_claims_pkey do update set body_hash=excluded.body_hash,owner=excluded.owner,sender_id=excluded.sender_id,
    device=excluded.device,environment=excluded.environment,state='sending',lease_until=excluded.lease_until,expires_at=excluded.expires_at,message_id=null;
   result:='acquired';
  end if;
 elsif action='complete' and claim.owner is null then
  -- A locally accepted send can outlive daily cleanup during a long outage.
  insert into telegram_delivery_claims values(inst,dest,event,body,token,sender,input->>'device',input->>'environment',
   'sent',stamp,stamp+interval '1 day',(input->>'message_id')::bigint);
  result:='completed';
 elsif claim.owner=token and claim.state='sending' then
  if action='complete' then
   update telegram_delivery_claims c set state='sent',expires_at=stamp+interval '1 day',message_id=(input->>'message_id')::bigint
    where c.installation=inst and c.destination_hash=dest and c.event_hash=event;
   result:='completed';
  elsif action='renew' and claim.lease_until>stamp then
   update telegram_delivery_claims c set lease_until=stamp+interval '90 seconds',expires_at=stamp+interval '1 day'
    where c.installation=inst and c.destination_hash=dest and c.event_hash=event;
   result:='acquired';
  elsif action='release' then
   update telegram_delivery_claims c set lease_until=stamp where c.installation=inst and c.destination_hash=dest and c.event_hash=event;
   result:='released';why:='Send failed or delivery uncertain; automatic retry enabled';
  else result:='busy';why:='Lease expired';end if;
 else result:='busy';why:='Ownership lost';end if;
 insert into telegram_delivery_attempts values(inst,token,dest,event,body,sender,input->>'device',input->>'environment',result,why,(input->>'message_id')::bigint,stamp)
 on conflict on constraint telegram_delivery_attempts_pkey do update set outcome=excluded.outcome,reason=excluded.reason,
  message_id=coalesce(excluded.message_id,telegram_delivery_attempts.message_id),updated_at=excluded.updated_at;
 return jsonb_build_object('status',result,'reason',why,'sender_id',claim.sender_id,'device',claim.device,'environment',claim.environment);
end;
$$;
revoke all on function public.telegram_coordinate(uuid,jsonb) from public,anon,authenticated;
grant execute on function public.telegram_coordinate(uuid,jsonb) to service_role;
