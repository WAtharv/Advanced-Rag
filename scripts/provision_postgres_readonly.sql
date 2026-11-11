-- ============================================================================
-- Least-Privilege Read-Only Role Provisioning for Text2SQL
-- Enterprise Advanced RAG / Kubernetes SRE Copilot
-- ============================================================================
-- This script provisions a dedicated, restricted PostgreSQL user role specifically
-- for Text2SQL read-only query execution.
--
-- Security Guarantees:
-- 1. Explicit NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOBYPASSRLS.
-- 2. Explicit REVOKE of all privileges on public/internal schemas and sequences.
-- 3. Explicit GRANT SELECT only on the 5 approved operational tables:
--    - clusters
--    - nodes
--    - deployments
--    - incidents
--    - slo_breaches
-- 4. Explicit REVOKE on document registry and ingestion tables:
--    - document_metadata
--    - ingestion_runs
-- 5. Revocation of sequence access and future table/sequence default privileges.
-- 6. Read-only transaction enforcement and connection timeout defaults.
-- 7. Preflight audit query for DBA review of inherited PUBLIC permissions.
-- ============================================================================

-- 1. Create dedicated least-privilege role (replace password with secure vault secret)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'text2sql_readonly') THEN
        CREATE ROLE text2sql_readonly WITH 
            LOGIN 
            NOSUPERUSER 
            NOCREATEDB 
            NOCREATEROLE 
            NOINHERIT 
            NOBYPASSRLS 
            PASSWORD 'change_me_in_production';
    ELSE
        ALTER ROLE text2sql_readonly WITH 
            NOSUPERUSER 
            NOCREATEDB 
            NOCREATEROLE 
            NOINHERIT 
            NOBYPASSRLS;
    END IF;
END
$$;

-- 2. Restrict role to read-only transactions and bounded execution by default
ALTER ROLE text2sql_readonly SET default_transaction_read_only = ON;
ALTER ROLE text2sql_readonly SET statement_timeout = '15s';

-- 3. Revoke all default permissions on database, schema, tables, and sequences
REVOKE ALL ON DATABASE CURRENT_DATABASE() FROM text2sql_readonly;
REVOKE CREATE ON SCHEMA public FROM text2sql_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM text2sql_readonly;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM text2sql_readonly;

-- 4. Grant USAGE on public schema
GRANT USAGE ON SCHEMA public TO text2sql_readonly;

-- 5. Explicitly grant SELECT ONLY on the 5 authorized operational tables
GRANT SELECT ON TABLE clusters TO text2sql_readonly;
GRANT SELECT ON TABLE nodes TO text2sql_readonly;
GRANT SELECT ON TABLE deployments TO text2sql_readonly;
GRANT SELECT ON TABLE incidents TO text2sql_readonly;
GRANT SELECT ON TABLE slo_breaches TO text2sql_readonly;

-- 6. Explicitly ensure no access to internal ingestion/registry tables
REVOKE ALL ON TABLE document_metadata FROM text2sql_readonly;
REVOKE ALL ON TABLE ingestion_runs FROM text2sql_readonly;

-- 7. Ensure future tables and sequences do not automatically grant access
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM text2sql_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM text2sql_readonly;

-- 8. DBA Preflight & Hardening Check: Inspect and remediate unexpected PUBLIC grants
-- Note for DBA: PostgreSQL grants certain permissions to PUBLIC by default.
-- Run the following preflight query to verify whether internal tables have PUBLIC grants:
-- SELECT grantee, table_schema, table_name, privilege_type 
-- FROM information_schema.table_privileges 
-- WHERE table_name IN ('document_metadata', 'ingestion_runs') AND grantee = 'PUBLIC';

-- If preflight check shows PUBLIC permissions exist on sensitive tables, execute:
REVOKE ALL ON TABLE document_metadata FROM PUBLIC;
REVOKE ALL ON TABLE ingestion_runs FROM PUBLIC;
