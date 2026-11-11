"""Unit and integration tests for Stage 5 Tasks 62 & 63.

Task 62: SQL Result Formatting, Dialect-Aware Pagination & SRE Summaries.
Task 63: Alembic Database Migrations & Persistent Approval Audit Table.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from advanced_rag.api.main import app
from advanced_rag.config import Settings
from advanced_rag.db.models import Base
from advanced_rag.models import ApprovalStatus
from advanced_rag.text2sql import executor
from advanced_rag.text2sql.audit import ApprovalAuditStore
from advanced_rag.text2sql.database import QueryResult
from alembic import command

# ---------------------------------------------------------------------------
# Task 62: Result Formatting, Pagination & SRE Summaries
# ---------------------------------------------------------------------------


def test_sql_paginate_ast_bounds():
    """Test AST-based pagination logic, clamping, and offset calculations."""
    base_sql = "SELECT id, name FROM clusters WHERE status = 'healthy'"

    # Page 1, size 10 -> LIMIT 10 OFFSET 0
    sql1, page1, size1, offset1 = executor.paginate_sql(
        base_sql, page=1, page_size=10, dialect="sqlite"
    )
    assert page1 == 1
    assert size1 == 10
    assert offset1 == 0
    assert "LIMIT 10" in sql1.upper()
    assert "OFFSET 0" in sql1.upper()

    # Page 3, size 25 -> LIMIT 25 OFFSET 50
    sql3, page3, size3, offset3 = executor.paginate_sql(
        base_sql, page=3, page_size=25, dialect="sqlite"
    )
    assert page3 == 3
    assert size3 == 25
    assert offset3 == 50
    assert "LIMIT 25" in sql3.upper()
    assert "OFFSET 50" in sql3.upper()

    # Clamping negative pages and excessive page sizes
    sql_clamp, page_clamp, size_clamp, offset_clamp = executor.paginate_sql(
        base_sql, page=-5, page_size=99999, dialect="sqlite", max_page_size=200
    )
    assert page_clamp == 1
    assert size_clamp == 200
    assert offset_clamp == 0
    assert "LIMIT 200" in sql_clamp.upper()


def test_sql_paginate_dialect_aware():
    """Verify that pagination uses SQLGlot to generate proper dialect syntax."""
    query = "SELECT id, name FROM nodes ORDER BY id"
    sqlite_p, _, _, _ = executor.paginate_sql(
        query, page=2, page_size=5, dialect="sqlite"
    )
    pg_p, _, _, _ = executor.paginate_sql(
        query, page=2, page_size=5, dialect="postgres"
    )

    assert "LIMIT 5" in sqlite_p.upper()
    assert "OFFSET 5" in sqlite_p.upper()
    assert "LIMIT 5" in pg_p.upper()
    assert "OFFSET 5" in pg_p.upper()


def test_sql_infer_column_types():
    """Test column type inference from cursor or row contents."""
    columns = ["id", "ratio", "active", "created_at", "name", "config"]
    rows = [
        [1, 0.95, True, datetime.now(UTC), "cluster-prod", {"env": "prod"}],
        [2, 0.99, False, datetime.now(UTC), "cluster-stage", {"env": "stage"}],
    ]
    types = executor._infer_column_types(columns, rows)
    assert types["id"] == "INTEGER"
    assert types["ratio"] == "FLOAT"
    assert types["active"] == "BOOLEAN"
    assert types["created_at"] == "TIMESTAMP"
    assert types["name"] == "TEXT"
    assert types["config"] == "JSON"


def test_sre_summary_generation():
    """Test deterministic human-readable SRE summaries."""
    # 1. Single known table (clusters)
    res_clusters = QueryResult(columns=["name"], rows=[["prod-1"], ["prod-2"]])
    sum1 = executor.generate_sre_summary(res_clusters, tables=["clusters"])
    assert "Found 2 clusters." in sum1

    # 2. Single item (incident)
    res_incident = QueryResult(columns=["id"], rows=[[101]])
    sum2 = executor.generate_sre_summary(res_incident, tables=["incidents"])
    assert "Found 1 incident." in sum2

    # 3. Multi-table query
    res_multi = QueryResult(
        columns=["c", "d"], rows=[["prod", "api"], ["stage", "worker"]]
    )
    sum3 = executor.generate_sre_summary(res_multi, tables=["clusters", "deployments"])
    assert "Returned 2 records across 2 tables (clusters, deployments)." in sum3

    # 4. Empty result
    res_empty = QueryResult(columns=["id"], rows=[])
    sum4 = executor.generate_sre_summary(res_empty, tables=["slo_breaches"])
    assert sum4 == "No records found matching query."

    # 5. Truncated result
    res_trunc = QueryResult(
        columns=["id"],
        rows=[[1]],
        truncated=True,
        truncation_reason="Row limit reached",
        execution_duration_ms=12.4,
    )
    sum5 = executor.generate_sre_summary(res_trunc, tables=["nodes"])
    assert "Found 1 node." in sum5
    assert "(Notice: Row limit reached)" in sum5
    assert "[Executed in 12.4ms]" in sum5


def test_executor_returns_structured_result():
    """Verify executor returns enriched QueryResult with duration, types, and pagination."""
    settings = Settings(
        sql_url="sqlite:///data/ops.db",
        sql_readonly_url="sqlite:///data/ops.db",
        enable_text2sql=True,
        sql_row_limit=5,
    )
    res = executor.execute(
        "SELECT id, name, environment FROM clusters",
        settings=settings,
        tables=["clusters"],
    )
    assert isinstance(res, QueryResult)
    assert len(res.columns) == 3
    assert res.row_count > 0
    assert "id" in res.column_types
    assert res.column_types["id"] == "INTEGER"
    assert res.execution_duration_ms >= 0.0
    assert "cluster" in res.summary.lower()


def test_executor_wide_table_zero_rows_cell_budget():
    """When a wide table exceeds max cells in a single row, return 0 rows with explicit metadata."""
    settings = Settings(
        sql_url="sqlite:///data/ops.db",
        sql_readonly_url="sqlite:///data/ops.db",
        enable_text2sql=True,
        sql_max_result_cells=2,  # less than the 3 columns
    )
    res = executor.execute(
        "SELECT id, name, environment FROM clusters", settings=settings
    )
    assert res.row_count == 0
    assert res.truncated is True
    assert "exceeded by single row width" in (res.truncation_reason or "")
    assert res.summary.startswith("Query returned 0 rows")


def test_api_sql_paginate_endpoint():
    """Test POST /sql/paginate via FastAPI test client."""
    client = TestClient(app)
    payload = {
        "sql": "SELECT id, name FROM clusters",
        "page": 1,
        "page_size": 2,
        "dialect": "sqlite",
    }
    resp = client.post("/sql/paginate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "columns" in data
    assert "rows" in data
    assert len(data["rows"]) <= 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert "summary" in data


# ---------------------------------------------------------------------------
# Task 63: Alembic Migrations & Persistent Approval Audit Table
# ---------------------------------------------------------------------------


def _get_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [r[0] for r in rows]


def test_alembic_init_upgrade_and_downgrade():
    """Run full Alembic migration cycle on an isolated temporary SQLite database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "alembic_test.db")
        db_url = f"sqlite:///{db_path}"

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", db_url)

        # 1. Upgrade to head
        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        tables = _get_tables(conn)
        conn.close()

        assert "alembic_version" in tables
        assert "document_metadata" in tables
        assert "ingestion_runs" in tables
        assert "approval_audit" in tables

        # 2. Downgrade to 001_baseline_registry
        command.downgrade(cfg, "001_baseline_registry")
        conn = sqlite3.connect(db_path)
        tables_down = _get_tables(conn)
        conn.close()

        assert "approval_audit" not in tables_down
        assert "document_metadata" in tables_down

        # 3. Downgrade to base
        command.downgrade(cfg, "base")
        conn = sqlite3.connect(db_path)
        tables_base = _get_tables(conn)
        conn.close()

        assert "document_metadata" not in tables_base
        assert "ingestion_runs" not in tables_base

        # 4. Re-upgrade to head
        command.upgrade(cfg, "head")
        conn = sqlite3.connect(db_path)
        tables_reup = _get_tables(conn)
        conn.close()
        assert "approval_audit" in tables_reup


def test_alembic_migration_preserves_existing_data():
    """Verify that Alembic migrations preserve pre-existing operational tables and rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "existing_data.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO clusters (id, name) VALUES (1, 'prod-us-east')")
        conn.commit()
        conn.close()

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        command.upgrade(cfg, "head")

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT id, name FROM clusters").fetchone()
        assert row == (1, "prod-us-east")
        tables = _get_tables(conn)
        conn.close()

        assert "clusters" in tables
        assert "approval_audit" in tables


def test_persistent_approval_audit_store_lifecycle():
    """Verify persistent audit logging survives store recreation and process restart simulation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "audit_test.db")
        db_url = f"sqlite:///{db_path}"

        # Initialize schema including approval_audit table
        test_engine = create_engine(db_url, future=True)
        Base.metadata.create_all(bind=test_engine)

        # Patch session engine to use test_engine
        from advanced_rag.db import session as db_session

        orig_engine = db_session._global_engine
        orig_factory = db_session._global_session_factory
        db_session._global_engine = test_engine
        db_session._global_session_factory = None

        try:
            store1 = ApprovalAuditStore(persist_db=True)
            prop_id = "test-prop-123"
            thread_id = "test-thread-456"

            # 1. Record proposal
            store1.record_proposal(
                proposal=None,
                thread_id=thread_id,
                proposal_id=prop_id,
                sql="SELECT * FROM clusters",
                sql_hash="hash-12345",
                dialect="sqlite",
                tables=["clusters"],
            )

            # 2. Record decision
            store1.record_decision(
                prop_id,
                ApprovalStatus.APPROVED,
                actor="sre_oncall",
            )

            # 3. Record execution start and completion
            store1.record_execution_start(prop_id)
            store1.record_execution_end(
                prop_id,
                ApprovalStatus.COMPLETED,
                outcome="5 rows returned",
            )

            # Recreate store instance (simulates process restart / new worker)
            store2 = ApprovalAuditStore(persist_db=True)
            rec = store2.get(prop_id)
            assert rec is not None
            assert rec.proposal_id == prop_id
            assert rec.thread_id == thread_id
            assert rec.actor == "sre_oncall"
            assert rec.status == ApprovalStatus.COMPLETED.value
            assert rec.execution_outcome == "5 rows returned"

            # Verify query by thread
            by_thread = store2.get_by_thread(thread_id)
            assert len(by_thread) == 1
            assert by_thread[0].proposal_id == prop_id

            # Verify recent listing
            recent = store2.list_recent(limit=10)
            assert any(r.proposal_id == prop_id for r in recent)

        finally:
            db_session._global_engine = orig_engine
            db_session._global_session_factory = orig_factory
            test_engine.dispose()


def test_concurrent_approval_audit_writes():
    """Verify thread-safety and DB integrity during concurrent audit event logging."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "concurrent_audit.db")
        db_url = f"sqlite:///{db_path}"

        test_engine = create_engine(
            db_url, future=True, connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=test_engine)

        from advanced_rag.db import session as db_session

        orig_engine = db_session._global_engine
        orig_factory = db_session._global_session_factory
        db_session._global_engine = test_engine
        db_session._global_session_factory = None

        store = ApprovalAuditStore(persist_db=True)
        errors: list[Exception] = []

        def worker(worker_id: int):
            try:
                pid = f"prop-concurrent-{worker_id}"
                tid = f"thread-concurrent-{worker_id}"
                store.record_proposal(
                    proposal=None,
                    thread_id=tid,
                    proposal_id=pid,
                    sql=f"SELECT {worker_id}",
                    sql_hash=f"hash-{worker_id}",
                    tables=["clusters"],
                )
                store.record_decision(
                    pid, ApprovalStatus.APPROVED, actor=f"worker-{worker_id}"
                )
                store.record_execution_start(pid)
                store.record_execution_end(pid, ApprovalStatus.COMPLETED, outcome="ok")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        # Verify all 10 proposals exist
        for i in range(10):
            pid = f"prop-concurrent-{i}"
            assert store.get(pid) is not None

        db_session._global_engine = orig_engine
        db_session._global_session_factory = orig_factory
        test_engine.dispose()
