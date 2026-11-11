"""Tests for Stage 5 Tasks 58 and 59: SQL AST Validation, Database Read-Only Permissions & Allowlists."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, text

from advanced_rag.config import Settings
from advanced_rag.models import Route, SqlProposal
from advanced_rag.text2sql import database, executor
from advanced_rag.text2sql.executor import SqlRejected, execute, validate
from advanced_rag.text2sql.security import (
    FORBIDDEN_TABLES,
    OPERATIONAL_COLUMNS,
    OPERATIONAL_SCHEMAS,
    OPERATIONAL_TABLES,
    SqlValidationResult,
    validate_sql_ast,
)


# =============================================================================
# PART 1: AST Parser Tests (Task 58)
# =============================================================================


def test_ast_valid_simple_select():
    sql = "SELECT id, name, region FROM clusters WHERE environment = 'production'"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert res.statement_type == "SELECT"
    assert "clusters" in res.tables
    assert res.error is None


def test_ast_select_with_where():
    sql = "SELECT id, service, severity FROM incidents WHERE severity = 'sev1' AND customer_impacting = 1"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert "incidents" in res.tables


def test_ast_select_with_join():
    sql = """
        SELECT i.id, i.service, c.name, c.region
        FROM incidents i
        JOIN clusters c ON c.id = i.cluster_id
        WHERE i.severity = 'sev1'
    """
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert set(res.tables) == {"incidents", "clusters"}


def test_ast_select_with_aggregation_and_having():
    sql = """
        SELECT service, COUNT(*) AS incident_cnt, AVG(duration_minutes) AS avg_dur
        FROM incidents
        GROUP BY service
        HAVING incident_cnt > 2
        ORDER BY incident_cnt DESC
    """
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert "incidents" in res.tables


def test_ast_select_with_cte():
    sql = """
        WITH prod_clusters AS (
            SELECT id, name FROM clusters WHERE environment = 'production'
        )
        SELECT p.name, n.name AS node_name, n.cpu_cores
        FROM nodes n
        JOIN prod_clusters p ON p.id = n.cluster_id
        ORDER BY n.cpu_cores DESC
    """
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert set(res.tables) == {"nodes", "clusters"}


def test_ast_nested_select_subquery():
    sql = """
        SELECT id, name FROM clusters
        WHERE id IN (
            SELECT cluster_id FROM incidents WHERE severity = 'sev1'
        )
    """
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert set(res.tables) == {"clusters", "incidents"}


def test_ast_valid_aliases():
    sql = "SELECT dep.id, dep.service, dep.version FROM deployments AS dep WHERE dep.status = 'succeeded'"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is True
    assert "deployments" in res.tables


def test_ast_invalid_table_reference():
    sql = "SELECT * FROM kubernetes_pods WHERE namespace = 'kube-system'"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "kubernetes_pods" in res.error
    assert "not exist in the schema" in res.error.lower() or "not authorized" in res.error.lower()


def test_ast_invalid_column_reference():
    sql = "SELECT id, nonexistent_column FROM clusters"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "nonexistent_column" in res.error


def test_ast_multiple_statements_rejected():
    sql = "SELECT id FROM clusters; DROP TABLE nodes;"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "multiple statements" in res.error.lower()


def test_ast_insert_rejected():
    sql = "INSERT INTO clusters (id, name, region, environment, k8s_version, node_count, created_at) VALUES (99, 'evil', 'us-east-1', 'prod', '1.30', 1, '2026-01-01')"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "INSERT" in res.error.upper() or "FORBIDDEN" in res.error.upper()


def test_ast_update_rejected():
    sql = "UPDATE clusters SET environment = 'dev' WHERE id = 1"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "UPDATE" in res.error.upper() or "FORBIDDEN" in res.error.upper()


def test_ast_delete_rejected():
    sql = "DELETE FROM incidents WHERE severity = 'sev3'"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "DELETE" in res.error.upper() or "FORBIDDEN" in res.error.upper()


def test_ast_drop_rejected():
    sql = "DROP TABLE nodes"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "DROP" in res.error.upper() or "FORBIDDEN" in res.error.upper()


def test_ast_alter_and_create_rejected():
    res1 = validate_sql_ast("ALTER TABLE nodes ADD COLUMN secret_key TEXT", dialect="sqlite")
    assert res1.is_valid is False

    res2 = validate_sql_ast("CREATE TABLE backdoor (id INT)", dialect="sqlite")
    assert res2.is_valid is False


def test_ast_pragma_rejected():
    sql = "PRAGMA table_info(clusters)"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "PRAGMA" in res.error.upper() or "FORBIDDEN" in res.error.upper()


def test_ast_attach_detach_rejected():
    res1 = validate_sql_ast("ATTACH DATABASE 'malicious.db' AS evil", dialect="sqlite")
    assert res1.is_valid is False

    res2 = validate_sql_ast("DETACH DATABASE evil", dialect="sqlite")
    assert res2.is_valid is False


def test_ast_dangerous_function_rejected():
    sql = "SELECT load_extension('exploit.so') FROM clusters"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "load_extension" in res.error.lower()


def test_ast_dialect_specific_parsing():
    # SQLite rejects ILIKE
    res_sqlite = validate_sql_ast("SELECT * FROM clusters WHERE name ILIKE '%prod%'", dialect="sqlite")
    assert res_sqlite.is_valid is False
    assert "ILIKE" in res_sqlite.error

    # PostgreSQL allows ILIKE
    res_pg = validate_sql_ast("SELECT * FROM clusters WHERE name ILIKE '%prod%'", dialect="postgresql")
    assert res_pg.is_valid is True


def test_ast_malformed_sql_handling():
    res = validate_sql_ast("SELECT FROM WHERE GROUP", dialect="sqlite")
    assert res.is_valid is False
    assert "syntax error" in res.error.lower()


# =============================================================================
# PART 2: Schema, Table and Column Allowlist Tests (Task 59)
# =============================================================================


def test_allowlist_all_five_operational_tables():
    for table in OPERATIONAL_TABLES:
        sql = f"SELECT * FROM {table} LIMIT 1"
        res = validate_sql_ast(sql, dialect="sqlite")
        assert res.is_valid is True, f"Allowlist falsely rejected operational table: {table}"


def test_allowlist_unauthorized_document_metadata_access():
    sql = "SELECT id, document_id, title FROM document_metadata"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "document_metadata" in res.error
    assert "strictly forbidden" in res.error.lower() or "not authorized" in res.error.lower()


def test_allowlist_unauthorized_ingestion_runs_access():
    sql = "SELECT run_id, status FROM ingestion_runs"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "ingestion_runs" in res.error


def test_allowlist_unauthorized_sqlite_master_access():
    sql = "SELECT name, sql FROM sqlite_master"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "sqlite_master" in res.error


def test_allowlist_unauthorized_columns():
    sql = "SELECT id, password_hash FROM nodes"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "password_hash" in res.error


def test_allowlist_unauthorized_schema_reference():
    sql = "SELECT * FROM secret_schema.clusters"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "Unauthorized schema" in res.error


def test_allowlist_join_involving_unauthorized_table():
    sql = "SELECT c.name, d.document_id FROM clusters c JOIN document_metadata d ON c.id = d.id"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "document_metadata" in res.error


def test_allowlist_cte_referencing_unauthorized_table():
    sql = """
        WITH secret_data AS (
            SELECT * FROM ingestion_runs
        )
        SELECT * FROM secret_data
    """
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "ingestion_runs" in res.error


def test_allowlist_nested_query_referencing_unauthorized_table():
    sql = "SELECT name FROM clusters WHERE id IN (SELECT id FROM document_metadata)"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "document_metadata" in res.error


def test_allowlist_fails_closed_when_empty(monkeypatch):
    monkeypatch.setattr("advanced_rag.text2sql.security.OPERATIONAL_TABLES", set())
    res = validate_sql_ast("SELECT id FROM clusters", dialect="sqlite")
    assert res.is_valid is False
    assert "Security policy error" in res.error or "not configured" in res.error.lower()


# =============================================================================
# PART 3: Database Permission Tests (Dedicated Read-Only Engine) (Task 59)
# =============================================================================


@pytest.fixture
def isolated_test_db(tmp_path: Path):
    """Create a completely isolated temporary SQLite database for read-only testing."""
    db_file = tmp_path / "test_ops.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("CREATE TABLE clusters (id INT PRIMARY KEY, name TEXT, environment TEXT);")
    cur.execute("INSERT INTO clusters VALUES (1, 'prod-1', 'production');")
    conn.commit()
    conn.close()
    return db_file


def test_readonly_select_succeeds(isolated_test_db: Path):
    url = f"sqlite+pysqlite:///file:{isolated_test_db.as_posix()}?mode=ro&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_readonly(dbapi_conn, record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.close()

    with engine.connect() as conn:
        res = conn.execute(text("SELECT name FROM clusters WHERE id = 1"))
        assert res.scalar() == "prod-1"


def test_database_denies_insert(isolated_test_db: Path):
    url = f"sqlite+pysqlite:///file:{isolated_test_db.as_posix()}?mode=ro&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_readonly(dbapi_conn, record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.close()

    with engine.connect() as conn:
        with pytest.raises(Exception) as exc_info:
            conn.execute(text("INSERT INTO clusters VALUES (2, 'bad', 'dev')"))
        assert "readonly database" in str(exc_info.value).lower()


def test_database_denies_update(isolated_test_db: Path):
    url = f"sqlite+pysqlite:///file:{isolated_test_db.as_posix()}?mode=ro&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_readonly(dbapi_conn, record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.close()

    with engine.connect() as conn:
        with pytest.raises(Exception) as exc_info:
            conn.execute(text("UPDATE clusters SET environment = 'compromised' WHERE id = 1"))
        assert "readonly database" in str(exc_info.value).lower()


def test_database_denies_delete(isolated_test_db: Path):
    url = f"sqlite+pysqlite:///file:{isolated_test_db.as_posix()}?mode=ro&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_readonly(dbapi_conn, record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.close()

    with engine.connect() as conn:
        with pytest.raises(Exception) as exc_info:
            conn.execute(text("DELETE FROM clusters WHERE id = 1"))
        assert "readonly database" in str(exc_info.value).lower()


def test_database_denies_drop_table(isolated_test_db: Path):
    url = f"sqlite+pysqlite:///file:{isolated_test_db.as_posix()}?mode=ro&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_readonly(dbapi_conn, record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.close()

    with engine.connect() as conn:
        with pytest.raises(Exception) as exc_info:
            conn.execute(text("DROP TABLE clusters"))
        assert "readonly database" in str(exc_info.value).lower()


def test_database_denies_create_table(isolated_test_db: Path):
    url = f"sqlite+pysqlite:///file:{isolated_test_db.as_posix()}?mode=ro&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_readonly(dbapi_conn, record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.close()

    with engine.connect() as conn:
        with pytest.raises(Exception) as exc_info:
            conn.execute(text("CREATE TABLE test_table (id INT)"))
        assert "readonly database" in str(exc_info.value).lower()


def test_normal_application_database_writes_remain_operational(isolated_test_db: Path):
    """Confirm the normal application engine continues to have write access."""
    write_url = f"sqlite+pysqlite:///{isolated_test_db.as_posix()}"
    write_engine = create_engine(write_url, future=True, connect_args={"check_same_thread": False})

    with write_engine.connect() as conn:
        conn.execute(text("INSERT INTO clusters VALUES (10, 'write-test', 'staging');"))
        conn.commit()

    with write_engine.connect() as conn:
        res = conn.execute(text("SELECT count(*) FROM clusters WHERE id = 10;"))
        assert res.scalar() == 1


def test_postgres_readonly_role_script_exists():
    script_path = Path("scripts/provision_postgres_readonly.sql")
    assert script_path.exists(), "PostgreSQL read-only provisioning script must exist"
    content = script_path.read_text(encoding="utf-8")
    assert "text2sql_readonly" in content
    assert "GRANT SELECT ON TABLE clusters" in content
    assert "REVOKE ALL ON TABLE document_metadata" in content


# =============================================================================
# PART 4: Workflow Regression & Approval Revalidation Tests
# =============================================================================


def test_invalid_sql_raises_sql_rejected():
    with pytest.raises(SqlRejected) as exc:
        validate("UPDATE clusters SET node_count = 0")
    assert "forbidden" in str(exc.value).lower() or "operation" in str(exc.value).lower()


def test_executor_revalidates_and_refuses_modified_query(monkeypatch):
    """Confirm that execute() revalidates the SQL through AST check and rejects tampering."""
    with pytest.raises(SqlRejected):
        execute("DROP TABLE incidents")


def test_executor_executes_valid_query_using_readonly_engine():
    res = execute("SELECT id, name FROM clusters WHERE environment = 'production' LIMIT 5")
    assert len(res.columns) == 2
    assert "id" in res.columns
    assert "name" in res.columns
    assert len(res.rows) > 0


# =============================================================================
# PART 5: Security Remediation Tests (Tasks 58 + 59 Audit Fixes)
# =============================================================================


def test_postgres_missing_readonly_dsn_fails_closed():
    """Verify that when PostgreSQL is configured without postgres_readonly_dsn, it fails closed."""
    settings = Settings(
        postgres_dsn="postgresql+psycopg://sre_admin:secret@pg-prod.internal:5432/ops",
        postgres_readonly_dsn=None,
    )
    with pytest.raises(ValueError) as exc:
        _ = settings.sql_readonly_url
    assert "POSTGRES_READONLY_DSN must be explicitly configured" in str(exc.value)
    assert "strictly forbidden" in str(exc.value)


def test_postgres_with_both_dsns_uses_readonly_dsn():
    """Verify that when both DSNs are configured, sql_readonly_url returns the readonly DSN."""
    settings = Settings(
        postgres_dsn="postgresql+psycopg://sre_admin:secret@pg-prod.internal:5432/ops",
        postgres_readonly_dsn="postgresql+psycopg://text2sql_ro:ro_secret@pg-prod.internal:5432/ops",
    )
    url = settings.sql_readonly_url
    assert "text2sql_ro" in url
    assert "sre_admin" not in url


def test_sqlite_readonly_url_configuration(tmp_path: Path):
    """Verify that SQLite config returns the dedicated read-only URI."""
    db_file = tmp_path / "test.db"
    settings = Settings(
        postgres_dsn=None,
        postgres_readonly_dsn=None,
        sqlite_path=db_file,
    )
    url = settings.sql_readonly_url
    assert "mode=ro" in url
    assert "uri=true" in url


def test_ast_rejects_select_into_new_table():
    """Verify SELECT ... INTO a new table is rejected by AST validation."""
    sql = "SELECT * INTO new_table FROM clusters"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "INTO" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_select_into_existing_operational_table():
    """Verify SELECT ... INTO an existing operational table is rejected by AST validation."""
    sql = "SELECT * INTO clusters FROM nodes"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "INTO" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_select_into_temp_table():
    """Verify SELECT ... INTO TEMP table is rejected by AST validation."""
    sql = "SELECT * INTO TEMP temp_clusters FROM clusters"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "INTO" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_select_into_inside_cte():
    """Verify SELECT ... INTO inside a CTE is rejected by AST validation."""
    sql = "WITH cte AS (SELECT * INTO leaked_table FROM clusters) SELECT * FROM cte"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "INTO" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_select_for_update_postgres():
    """Verify SELECT ... FOR UPDATE is rejected in PostgreSQL dialect."""
    sql = "SELECT * FROM clusters FOR UPDATE"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "LOCK" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_select_for_update_sqlite():
    """Verify SELECT ... FOR UPDATE is rejected in SQLite dialect."""
    sql = "SELECT * FROM clusters FOR UPDATE"
    res = validate_sql_ast(sql, dialect="sqlite")
    assert res.is_valid is False
    assert "LOCK" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_select_for_share_postgres():
    """Verify SELECT ... FOR SHARE is rejected in PostgreSQL dialect."""
    sql = "SELECT * FROM clusters FOR SHARE"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "LOCK" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_nested_locking_select():
    """Verify row locking inside nested subqueries is rejected."""
    sql = "SELECT * FROM clusters WHERE id IN (SELECT cluster_id FROM nodes FOR UPDATE)"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "LOCK" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_cte_locking_select():
    """Verify row locking inside CTE expressions is rejected."""
    sql = "WITH cte AS (SELECT * FROM clusters FOR UPDATE) SELECT * FROM cte"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "LOCK" in res.error or "forbidden" in res.error.lower()


def test_ast_rejects_union_locking_select():
    """Verify row locking inside UNION branches is rejected."""
    sql = "SELECT * FROM clusters UNION SELECT * FROM clusters FOR UPDATE"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "LOCK" in res.error or "forbidden" in res.error.lower()


@pytest.mark.parametrize(
    "sql,dialect,forbidden_fn",
    [
        ("SELECT nextval('my_seq') FROM clusters", "postgres", "nextval"),
        ("SELECT setval('my_seq', 100) FROM clusters", "postgres", "setval"),
        ("SELECT set_config('work_mem', '1GB', false) FROM clusters", "postgres", "set_config"),
        ("SELECT pg_advisory_lock(12345) FROM clusters", "postgres", "pg_advisory_lock"),
        ("SELECT pg_sleep(5) FROM clusters", "postgres", "pg_sleep"),
        ("SELECT pg_read_file('/etc/passwd') FROM clusters", "postgres", "pg_read_file"),
        ("SELECT dblink('host=x', 'SELECT 1') FROM clusters", "postgres", "dblink"),
        ("SELECT load_extension('evil.so') FROM clusters", "sqlite", "load_extension"),
        ("SELECT readfile('/etc/passwd') FROM clusters", "sqlite", "readfile"),
        ("SELECT writefile('/tmp/pwn', 'data') FROM clusters", "sqlite", "writefile"),
    ],
)
def test_ast_rejects_side_effecting_functions(sql, dialect, forbidden_fn):
    """Verify dangerous, file-access, and side-effecting functions are strictly rejected."""
    res = validate_sql_ast(sql, dialect=dialect)
    assert res.is_valid is False
    assert forbidden_fn in res.error or "not allowed" in res.error.lower() or "forbidden" in res.error.lower()


def test_ast_rejects_table_valued_functions():
    """Verify table-valued functions like generate_series are rejected."""
    sql = "SELECT generate_series(1, 10) FROM clusters"
    res = validate_sql_ast(sql, dialect="postgres")
    assert res.is_valid is False
    assert "generate_series" in res.error.lower() or "not allowed" in res.error.lower() or "table-valued" in res.error.lower()


def test_ast_rejects_unknown_functions_by_default():
    """Verify unknown or unapproved functions fail closed by default."""
    # sqrt is not in ALLOWED_FUNCTIONS
    res1 = validate_sql_ast("SELECT sqrt(node_count) FROM clusters", dialect="sqlite")
    assert res1.is_valid is False
    assert "not allowed by policy" in res1.error

    # custom unapproved function
    res2 = validate_sql_ast("SELECT custom_unrecognized_fn(id) FROM clusters", dialect="sqlite")
    assert res2.is_valid is False
    assert "not allowed by policy" in res2.error


def test_ast_accepts_safe_math_string_aggregate_date_functions():
    """Verify approved aggregate, string, math, and dialect-specific date functions are accepted."""
    safe_queries = [
        ("SELECT count(*), sum(cpu_cores), avg(memory_gb), min(cpu_cores), max(memory_gb) FROM nodes", "sqlite"),
        ("SELECT round(actual_pct, 1) FROM slo_breaches", "sqlite"),
        ("SELECT abs(duration_minutes), coalesce(resolved_at, 'open') FROM incidents", "sqlite"),
        ("SELECT lower(name), upper(region), substr(name, 1, 3), trim(name) FROM clusters", "sqlite"),
        ("SELECT strftime('%Y-%m', created_at), datetime('now', '-7 days') FROM clusters", "sqlite"),
        ("SELECT date_trunc('month', started_at), extract(year from started_at) FROM incidents", "postgres"),
        ("SELECT to_char(started_at, 'YYYY-MM'), now(), current_timestamp FROM incidents", "postgres"),
    ]
    for sql, dialect in safe_queries:
        res = validate_sql_ast(sql, dialect=dialect)
        assert res.is_valid is True, f"Safe query failed validation: {sql} -> {res.error}"


def test_postgres_role_script_hardened_privileges():
    """Verify the PostgreSQL provisioning script enforces strict least-privilege security."""
    script_path = Path("scripts/provision_postgres_readonly.sql")
    assert script_path.exists()
    content = script_path.read_text(encoding="utf-8")
    assert "NOINHERIT" in content
    assert "NOSUPERUSER" in content
    assert "NOCREATEDB" in content
    assert "NOCREATEROLE" in content
    assert "NOBYPASSRLS" in content
    assert "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM text2sql_readonly" in content
    assert "REVOKE ALL ON TABLE document_metadata FROM PUBLIC" in content
    assert "REVOKE ALL ON TABLE ingestion_runs FROM PUBLIC" in content

