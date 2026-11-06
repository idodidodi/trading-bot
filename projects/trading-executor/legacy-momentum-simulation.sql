CREATE OR REPLACE FUNCTION bot_runtime.momentum_opened(sig jsonb,bar jsonb) RETURNS bigint
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN sig->>'market'='stock' THEN
 (extract(epoch FROM (((to_timestamp((bar->>'start')::float8/1000) AT TIME ZONE 'America/New_York')::date+time '09:30') AT TIME ZONE 'America/New_York'))*1000)::bigint
 ELSE (bar->>'start')::bigint END;
$$;
CREATE OR REPLACE FUNCTION bot_runtime.momentum_outcome(sig jsonb,bars jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE
 baseline bigint:=greatest((sig->>'confirmed_at')::bigint,coalesce((sig->>'published_at')::bigint,0));
 b jsonb;first_bar jsonb;entry float8;opening bigint;d integer:=CASE WHEN sig->>'direction'='buy' THEN 1 ELSE -1 END;
 stop_price float8:=(sig->>'stop')::float8;target_price float8:=(sig->>'target')::float8;
 hold integer:=coalesce((sig->'rules'->>'max_hold')::int,10);cost float8:=coalesce((sig->'rules'->>'cost_bps')::float8,10);
 risk float8;reward float8;adverse float8:=0;favorable float8:=0;n integer:=0;status text;exit_price float8;
BEGIN
 SELECT value INTO first_bar FROM jsonb_array_elements(coalesce(bars,'[]')) WHERE bot_runtime.momentum_opened(sig,value)>=baseline ORDER BY (value->>'start')::bigint LIMIT 1;
 IF first_bar IS NULL THEN RETURN '{"status":"awaiting entry"}';END IF;
 entry:=(first_bar->>'open')::float8;opening:=bot_runtime.momentum_opened(sig,first_bar);
 IF sig->>'entry_open_at' IS NOT NULL AND opening<>(sig->>'entry_open_at')::bigint THEN RETURN '{"status":"unavailable","reason":"Entry-session candle missing; no later entry substituted"}';END IF;
 IF opening-baseline>(CASE WHEN sig->>'market'='stock' THEN 5 ELSE 2 END)*86400000::bigint THEN RETURN '{"status":"unavailable","reason":"Entry-session history missing; no later entry substituted"}';END IF;
 IF entry<(sig->>'entry_min')::float8 OR entry>(sig->>'entry_max')::float8 THEN RETURN jsonb_build_object('status','skipped gap','entry_at',opening);END IF;
 risk:=d*(entry-stop_price);reward:=d*(target_price-entry);
 IF risk<=0 OR reward/risk<1.5 THEN RETURN '{"status":"skipped reward/risk"}';END IF;
 FOR b IN SELECT value FROM jsonb_array_elements(bars) WHERE bot_runtime.momentum_opened(sig,value)>=baseline ORDER BY (value->>'start')::bigint LIMIT hold LOOP
  n:=n+1;
  adverse:=greatest(adverse,greatest(0,d*(entry-(b->>CASE WHEN d=1 THEN 'low' ELSE 'high' END)::float8))/entry*100);
  favorable:=greatest(favorable,greatest(0,d*((b->>CASE WHEN d=1 THEN 'high' ELSE 'low' END)::float8-entry))/entry*100);
  IF (d=1 AND (b->>'low')::float8<=stop_price) OR (d=-1 AND (b->>'high')::float8>=stop_price) THEN
   exit_price:=CASE WHEN d=1 THEN least((b->>'open')::float8,stop_price) ELSE greatest((b->>'open')::float8,stop_price) END;status:='stop';
  ELSIF (d=1 AND (b->>'high')::float8>=target_price) OR (d=-1 AND (b->>'low')::float8<=target_price) THEN exit_price:=target_price;status:='target';
  ELSIF n=hold THEN exit_price:=(b->>'close')::float8;status:='time exit';
  ELSE CONTINUE;END IF;
  RETURN jsonb_build_object('status',status,'entry_at',opening,'entry_price',entry,'exit_at',(b->>'end')::bigint,'exit_price',exit_price,'net_pct',d*(exit_price/entry-1)*100-2*cost/100,'adverse_pct',adverse,'favorable_pct',favorable);
 END LOOP;
 RETURN jsonb_build_object('status','open','entry_at',opening,'entry_price',entry,'adverse_pct',adverse,'favorable_pct',favorable);
END $$;
REVOKE ALL ON FUNCTION bot_runtime.momentum_opened(jsonb,jsonb),bot_runtime.momentum_outcome(jsonb,jsonb) FROM PUBLIC,anon,authenticated;
