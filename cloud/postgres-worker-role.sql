-- Admin-only, manual provisioning. One runtime installation per database.
BEGIN;
DO $$BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='trading_worker') THEN
  CREATE ROLE trading_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='trading_worker' AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit))
 OR EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='trading_worker') THEN
  RAISE EXCEPTION 'Do not reuse a privileged or role-member trading_worker account';
 END IF;
END $$;
GRANT USAGE ON SCHEMA bot_runtime TO trading_worker;
REVOKE ALL ON ALL TABLES IN SCHEMA bot_runtime FROM trading_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA bot_runtime TO trading_worker;
DO $$DECLARE r record;BEGIN
 FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='bot_runtime'
 AND tablename NOT IN ('runtime_control','schema_version','migration_runs','migration_audit','migration_holds','legacy_findings','legacy_artifacts') LOOP
  EXECUTE format('GRANT INSERT,UPDATE,DELETE ON bot_runtime.%I TO trading_worker',r.tablename);
 END LOOP;
END $$;
REVOKE UPDATE,DELETE ON bot_runtime.feedback_history,bot_runtime.config_history,bot_runtime.finding_keys FROM trading_worker;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA bot_runtime TO trading_worker;
GRANT EXECUTE ON FUNCTION bot_runtime.read_routes() TO trading_worker;
-- No browser role may read base tables or call worker functions directly.
REVOKE ALL ON SCHEMA bot_runtime FROM anon,authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA bot_runtime FROM anon,authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA bot_runtime FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION bot_runtime.read_routes() TO trading_worker;
COMMIT;
-- In psql, an administrator then chooses a private login password interactively:
-- ALTER ROLE trading_worker LOGIN;
-- \password trading_worker
-- Use pooler username trading_worker.PROJECT_REF from Supabase Connect; test it.
-- Never grant membership in postgres, service_role, anon or authenticated.
