-- Preserve response shape and authorization; avoid rebuilding all findings
-- just to join their small feedback and archive-state tables.
BEGIN;
DO $patch$
DECLARE
 definition text := pg_get_functiondef('bot_runtime.api(uuid,text,jsonb)'::regprocedure);
 old_feedback text := $old$LEFT JOIN runtime_records f ON f.kind='feedback' AND f.key=r.key$old$;
 new_feedback text := $new$LEFT JOIN (SELECT finding_id AS key,jsonb_build_object('finding_id',finding_id,'source',source,'evidence',evidence::jsonb,'rating',rating,'comment',comment,'revision',revision,'updated_at',updated_at) AS payload FROM finding_feedback) f ON f.key=r.key$new$;
 old_state text := $old$LEFT JOIN runtime_records s ON s.kind='finding_state' AND s.key=r.key$old$;
 new_state text := $new$LEFT JOIN (SELECT finding_id AS key,jsonb_build_object('finding_id',finding_id,'status',status,'revision',revision,'updated_at',updated_at) AS payload FROM finding_state) s ON s.key=r.key$new$;
BEGIN
 IF position(new_feedback IN definition)=0 THEN
  IF position(old_feedback IN definition)=0 THEN RAISE EXCEPTION 'Unexpected findings feedback join'; END IF;
  definition := replace(definition,old_feedback,new_feedback);
 END IF;
 IF position(new_state IN definition)=0 THEN
  IF position(old_state IN definition)=0 THEN RAISE EXCEPTION 'Unexpected findings status join'; END IF;
  definition := replace(definition,old_state,new_state);
 END IF;
 EXECUTE definition;
END $patch$;
COMMIT;
