-- LOCAL runtime database only, AFTER the runtime/auth UUID migration.
-- Run as the database administrator. Set a password privately outside this file.
BEGIN;
CREATE SCHEMA IF NOT EXISTS api_private;
REVOKE ALL ON SCHEMA api_private FROM PUBLIC;
CREATE TABLE IF NOT EXISTS api_private.clerk_identities (
 issuer text NOT NULL,
 subject text NOT NULL CHECK (subject LIKE 'user\_%' ESCAPE '\'),
 user_id uuid NOT NULL REFERENCES auth.users(id),
 enabled boolean NOT NULL DEFAULT true,
 PRIMARY KEY (issuer,subject),
 UNIQUE (issuer,user_id)
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='dashboard_gateway') THEN
  CREATE ROLE dashboard_gateway LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE CONNECTION LIMIT 4;
 END IF;
END $$;
GRANT authenticated TO dashboard_gateway;
GRANT USAGE ON SCHEMA api_private TO dashboard_gateway;
GRANT SELECT ON api_private.clerk_identities TO dashboard_gateway;
COMMIT;
