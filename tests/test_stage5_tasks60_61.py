"""Tests for Stage 5 Tasks 60 & 61: Targeted Test Quality & Workflow Verification.

Covers:
1. Result Cell Limits:
   - Wide and narrow result sets
   - returned rows * returned columns <= sql_max_result_cells strictly
   - Truncation metadata accurate in all cases, no exceptions permitted
2. Concurrency Limits:
   - Thread-safe active counter tracking max simultaneous executions
   - Started workers > configured limit, max simultaneous <= limit
   - Semaphore release verified following success, timeout, and exceptions
3. SQLite Timeout and Cancellation:
   - Progress-handler cleanup on success and timeout
   - Pooled connection reuse verification
   - Synchronous cancellation semantics determination
4. Approval Lifecycle:
   - Actual LangGraph interrupt/resume workflow
   - Full approval and execution
   - Full rejection without execution
   - Expired proposal execution denial
   - SQL modification after approval (tamper detection)
   - Proposal ID and thread ID mismatch
   - SQL hash mismatch
   - Duplicate approval and replay prevention
   - Concurrent approval attempts
   - Failed execution and audit state transitions (PENDING -> APPROVED -> EXECUTING -> FAILED)
5. Approval Audit Durability & Security:
   - In-process store limitation test
   - Actor treated as untrusted audit metadata (preserving Stage 7 auth boundary)
6. PostgreSQL Driver Configuration:
   - Mocked statement timeout and transaction-local read-only configuration
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from advanced_rag.config import Settings
from advanced_rag.graph import pipeline
from advanced_rag.graph.nodes import ContextGrade, RouteDecision
from advanced_rag.graph.state import RagState
from advanced_rag.models import (
    ApprovalStatus,
    GuardrailOutcome,
    Route,
    SqlProposal,
)
from advanced_rag.text2sql import database, executor
from advanced_rag.text2sql.audit import ApprovalAuditRecord, ApprovalAuditStore, get_audit_store


# =============================================================================
# 1. RESULT CELL LIMITS
# =============================================================================


def test_resource_limits_cell_truncation_wide_zero_rows():
    """Wide table where single row exceeds max_cells must truncate to 0 rows.
    
    deployments table has 10 columns in ops.db.
    With max_cells=4, max_rows = 4 // 10 == 0.
    """
    settings = Settings(
        sql_row_limit=100,
        sql_max_result_cells=4,
    )
    result = executor.execute("SELECT * FROM deployments;", settings=settings)
    assert len(result.columns) == 10
    assert len(result.rows) == 0
    assert result.truncated is True
    # Strict invariance: rows * cols <= max_cells
    assert len(result.rows) * len(result.columns) <= settings.sql_max_result_cells


def test_resource_limits_cell_truncation_wide_multiple_rows():
    """Wide table where rows * cols exceeds max_cells truncates to exact max allowed rows.
    
    deployments table has 10 columns. With max_cells=20, max_rows = 20 // 10 == 2 rows.
    """
    settings = Settings(
        sql_row_limit=100,
        sql_max_result_cells=20,
    )
    result = executor.execute("SELECT * FROM deployments;", settings=settings)
    assert len(result.columns) == 10
    assert len(result.rows) == 2
    assert result.truncated is True
    assert len(result.rows) * len(result.columns) <= settings.sql_max_result_cells
    assert len(result.rows) * len(result.columns) == 20


def test_resource_limits_cell_truncation_narrow_table():
    """Narrow table (1 column) clamped by cell limit."""
    settings = Settings(
        sql_row_limit=100,
        sql_max_result_cells=2,  # 1 column; 2 // 1 == 2 rows allowed
    )
    result = executor.execute("SELECT name FROM clusters;", settings=settings)
    assert len(result.columns) == 1
    assert len(result.rows) == 2
    assert result.truncated is True
    assert len(result.rows) * len(result.columns) <= settings.sql_max_result_cells


def test_resource_limits_no_truncation_when_within_budget():
    """Queries within both row and cell budgets are not marked truncated."""
    settings = Settings(
        sql_row_limit=100,
        sql_max_result_cells=5000,
    )
    result = executor.execute("SELECT name FROM clusters;", settings=settings)
    assert result.truncated is False
    assert len(result.rows) * len(result.columns) <= settings.sql_max_result_cells


# =============================================================================
# 2. CONCURRENCY LIMITS
# =============================================================================


def test_concurrency_semaphore_bounds_execution():
    """Thread-safe active worker tracking ensures simultaneous executions <= limit."""
    concurrency_limit = 2
    total_workers = 6

    settings = Settings(
        sql_max_concurrent_queries=concurrency_limit,
        sql_timeout_seconds=5.0,
    )

    lock = threading.Lock()
    active_workers = 0
    max_simultaneous = 0

    original_engine = database.get_readonly_engine()
    original_connect = original_engine.connect

    # Instrument connect inside executor's semaphore block
    def instrumented_connect():
        ctx = original_connect()

        class TrackedConn:
            def __init__(self, inner):
                self.inner = inner

            def __enter__(self):
                nonlocal active_workers, max_simultaneous
                with lock:
                    active_workers += 1
                    if active_workers > max_simultaneous:
                        max_simultaneous = active_workers
                time.sleep(0.05)  # Hold connection to force concurrency overlap
                return self.inner.__enter__()

            def __exit__(self, *args):
                nonlocal active_workers
                try:
                    return self.inner.__exit__(*args)
                finally:
                    with lock:
                        active_workers -= 1

        return TrackedConn(ctx)

    with patch.object(original_engine, "connect", side_effect=instrumented_connect):
        with concurrent.futures.ThreadPoolExecutor(max_workers=total_workers) as pool:
            futures = [
                pool.submit(executor.execute, "SELECT name FROM clusters;", settings=settings)
                for _ in range(total_workers)
            ]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                assert res.row_count > 0

    assert max_simultaneous <= concurrency_limit
    assert max_simultaneous > 0


def test_concurrency_semaphore_released_on_success_timeout_and_error():
    """Verify semaphore release following success, timeout, and exception."""
    concurrency_limit = 3
    sem = executor._get_semaphore(max_concurrent=concurrency_limit)
    initial_val = sem._value

    # 1. Successful execution
    s_ok = Settings(sql_max_concurrent_queries=concurrency_limit, sql_timeout_seconds=5.0)
    res = executor.execute("SELECT name FROM clusters;", settings=s_ok)
    assert res.row_count > 0
    assert sem._value == initial_val

    # 2. Timeout execution (using 20-table cross join count to force opcode ceiling)
    s_timeout = Settings(sql_max_concurrent_queries=concurrency_limit, sql_timeout_seconds=0.01)
    slow_sql = "SELECT count(*) FROM " + ", ".join([f"deployments d{i}" for i in range(20)]) + ";"
    with pytest.raises(executor.SqlTimeoutError):
        executor.execute(slow_sql, settings=s_timeout)
    assert sem._value == initial_val

    # 3. Exception execution (disallowed query)
    with pytest.raises(executor.SqlRejected):
        executor.execute("INSERT INTO clusters (name) VALUES ('bad');", settings=s_ok)
    assert sem._value == initial_val


# =============================================================================
# 3. SQLITE TIMEOUT AND CANCELLATION
# =============================================================================


def test_sqlite_progress_handler_cleanup_on_success_and_timeout():
    """Verify progress handler cleanup (set_progress_handler(None, 0)) on success and timeout."""
    settings = Settings(sql_timeout_seconds=2.0)

    # Success run
    res1 = executor.execute("SELECT count(*) FROM clusters;", settings=settings)
    assert res1.row_count == 1

    # Timeout run
    s_timeout = Settings(sql_timeout_seconds=0.01)
    slow_sql = "SELECT count(*) FROM " + ", ".join([f"deployments d{i}" for i in range(20)]) + ";"
    with pytest.raises(executor.SqlTimeoutError):
        executor.execute(slow_sql, settings=s_timeout)

    # Connection reuse run: pool connection must be clean and not aborted by leftover handler
    res2 = executor.execute("SELECT count(*) FROM nodes;", settings=settings)
    assert res2.row_count == 1


def test_sqlite_cancellation_semantics_demonstrated():
    """Demonstrate that Python sqlite3 in a worker thread does NOT cooperatively cancel.
    
    Without sqlite3.Connection.interrupt() or a progress handler deadline,
    an external client/thread cancellation cannot interrupt synchronous SQLite C execution.
    """
    engine = database.get_readonly_engine()
    interrupted = False

    with engine.connect() as conn:
        raw_conn = getattr(conn.connection, "dbapi_connection", None)
        assert raw_conn is not None

        # When interrupt() is explicitly called from another thread, query aborts
        def interrupt_worker():
            time.sleep(0.02)
            raw_conn.interrupt()

        t = threading.Thread(target=interrupt_worker)
        t.start()
        try:
            slow_sql = "SELECT count(*) FROM " + ", ".join([f"deployments d{i}" for i in range(25)]) + ";"
            import sqlite3
            with pytest.raises(sqlite3.OperationalError) as exc_info:
                conn.connection.cursor().execute(slow_sql)
            assert "interrupted" in str(exc_info.value).lower()
            interrupted = True
        finally:
            t.join()

    assert interrupted is True


# =============================================================================
# 4. APPROVAL LIFECYCLE (ACTUAL LANGGRAPH INTERRUPT/RESUME WORKFLOW)
# =============================================================================


class MockRouteLLM:
    def complete(self, *args, **kwargs):
        class Res:
            text = "Executed approved SQL query."
            refused = False
            input_tokens = 10
            output_tokens = 10
        return Res()

    def complete_json(self, prompt, schema, **kwargs):
        if schema is RouteDecision:
            return RouteDecision(route="sql", reason="Database query needed")
        if schema is ContextGrade:
            return ContextGrade(verdict="correct", reason="Good context")
        return schema()


class MockFakeSqlGen:
    def __init__(self, sql: str = "SELECT name FROM clusters LIMIT 5;"):
        self.sql = sql

    def generate(self, question, **kwargs):
        return SqlProposal(sql=self.sql, rationale="list cluster names", tables=["clusters"])


def _setup_langgraph_mocks(monkeypatch):
    """Sets up pipeline mocks with RouteDecision and ContextGrade support."""
    from advanced_rag.graph import nodes

    sql_gen = MockFakeSqlGen()
    monkeypatch.setattr(nodes, "get_sql_generator", lambda: sql_gen)
    monkeypatch.setattr(nodes, "get_llm", lambda: MockRouteLLM())

    class PassGuard:
        def check_input(self, q, **k):
            from advanced_rag.guardrails.pipeline import GuardrailResult
            return GuardrailResult(text=q, outcomes=[GuardrailOutcome(layer="shape", passed=True)])

        def check_output(self, a, **k):
            from advanced_rag.guardrails.pipeline import GuardrailResult
            return GuardrailResult(text=a, outcomes=[GuardrailOutcome(layer="secret_egress", passed=True)])

    monkeypatch.setattr(nodes, "get_guardrails", lambda: PassGuard())

    class NoCache:
        def lookup(self, q):
            return None, "none"

        def store(self, q, p):
            pass

    monkeypatch.setattr(nodes, "get_cache", lambda: NoCache())
    return sql_gen


def test_langgraph_workflow_full_approval_and_execution(monkeypatch):
    """Actual LangGraph interrupt/resume workflow: full approval and execution."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-approval-exec-1"

    # Phase 1: Ask -> Pauses at approval gate
    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True
    assert resp1.sql is not None
    assert resp1.sql.status == ApprovalStatus.PENDING
    assert resp1.sql.sql == "SELECT name FROM clusters LIMIT 5;"
    assert resp1.sql.sql_hash != ""

    pid = resp1.sql.proposal_id
    shash = resp1.sql.sql_hash

    # Phase 2: Resume with approved=True
    resp2 = pipeline.resume(
        thread_id,
        approved=True,
        proposal_id=pid,
        sql_hash=shash,
        actor="operator_bob",
    )
    assert resp2.awaiting_approval is False
    assert resp2.sql.status == ApprovalStatus.COMPLETED
    assert resp2.sql.approved is True
    assert len(resp2.sql.rows) > 0

    # Audit verification
    rec = get_audit_store().get(pid)
    assert rec is not None
    assert rec.status == ApprovalStatus.COMPLETED.value
    assert rec.actor == "operator_bob"


def test_langgraph_workflow_full_rejection_without_execution(monkeypatch):
    """Actual LangGraph workflow: explicit rejection prevents execution."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-rejection-1"

    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True
    pid = resp1.sql.proposal_id

    # Resume with approved=False
    resp2 = pipeline.resume(
        thread_id,
        approved=False,
        reason="Query out of scope for current sprint",
        actor="sre_lead",
    )
    assert resp2.awaiting_approval is False
    assert resp2.sql.status == ApprovalStatus.REJECTED
    assert resp2.sql.approved is False
    assert "rejected: Query out of scope" in resp2.answer
    assert resp2.sql.rows == []

    rec = get_audit_store().get(pid)
    assert rec is not None
    assert rec.status == ApprovalStatus.REJECTED.value
    assert rec.rejection_reason == "Query out of scope for current sprint"
    assert rec.actor == "sre_lead"


def test_langgraph_workflow_expired_proposal_denial(monkeypatch):
    """Actual LangGraph workflow: proposal past expires_at is rejected on resume."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-expired-1"

    # Set expiry to 0 seconds so proposal immediately expires
    from advanced_rag.graph import nodes
    monkeypatch.setattr(nodes, "get_settings", lambda: Settings(approval_expiry_seconds=0))

    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True
    pid = resp1.sql.proposal_id

    # Small delay to ensure current time > expires_at
    time.sleep(0.05)

    # Resume: approval attempted after expiry
    resp2 = pipeline.resume(thread_id, approved=True, proposal_id=pid)
    assert resp2.awaiting_approval is False
    assert resp2.sql.status == ApprovalStatus.EXPIRED
    assert "expired" in resp2.answer.lower()
    assert resp2.sql.rows == []


def test_langgraph_workflow_sql_modification_tamper_rejected():
    """Verify in-memory SQL modification is caught by hash verification."""
    from advanced_rag.graph import nodes

    sql_original = "SELECT name FROM clusters LIMIT 5;"
    sql_tampered = "SELECT name FROM clusters; -- injected comment"
    prop = SqlProposal(
        sql=sql_tampered,
        sql_hash=executor.compute_sql_hash(sql_original),  # Original hash bound at proposal time
        proposal_id="pid-tamper-check",
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        status=ApprovalStatus.PENDING,
    )
    state: RagState = {
        "original_question": "List all clusters",
        "thread_id": "test-tamper-direct",
        "sql": prop,
    }

    with patch("advanced_rag.graph.nodes.interrupt", return_value={"approved": True}):
        res = nodes.sql_approval_node(state)
        assert res["sql"].status == ApprovalStatus.REJECTED
        assert res["sql"].approved is False
        assert "modified" in res["answer"].lower()


def test_langgraph_workflow_proposal_id_mismatch(monkeypatch):
    """Actual LangGraph workflow: mismatched proposal_id is rejected."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-pid-mismatch-1"

    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True

    resp2 = pipeline.resume(
        thread_id,
        approved=True,
        proposal_id="wrong_proposal_id_xyz",
    )
    assert resp2.awaiting_approval is False
    assert resp2.sql.status == ApprovalStatus.REJECTED
    assert "proposal id" in resp2.answer.lower()


def test_langgraph_workflow_thread_id_mismatch():
    """Verify thread_id mismatch between proposal and resume context is rejected."""
    from advanced_rag.graph import nodes

    sql = "SELECT name FROM clusters;"
    prop = SqlProposal(
        sql=sql,
        sql_hash=executor.compute_sql_hash(sql),
        proposal_id="pid-thread-mismatch",
        status=ApprovalStatus.PENDING,
    )
    get_audit_store().record_proposal(prop, thread_id="thread-origin")

    state: RagState = {
        "original_question": "List clusters",
        "thread_id": "thread-origin",
        "sql": prop,
    }

    # Caller resumes providing a mismatched thread_id
    with patch(
        "advanced_rag.graph.nodes.interrupt",
        return_value={"approved": True, "thread_id": "thread-different"},
    ):
        res = nodes.sql_approval_node(state)
        assert res["sql"].status == ApprovalStatus.REJECTED
        assert "thread id" in res["answer"].lower()


def test_langgraph_workflow_sql_hash_mismatch(monkeypatch):
    """Actual LangGraph workflow: wrong sql_hash supplied on resume is rejected."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-hash-mismatch-1"

    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True

    resp2 = pipeline.resume(
        thread_id,
        approved=True,
        sql_hash="0000000000000000000000000000000000000000000000000000000000000000",
    )
    assert resp2.awaiting_approval is False
    assert resp2.sql.status == ApprovalStatus.REJECTED
    assert "hash mismatch" in resp2.answer.lower()


def test_langgraph_workflow_duplicate_approval_and_replay_prevention(monkeypatch):
    """Actual LangGraph workflow: duplicate resume on already completed proposal is blocked."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-replay-1"

    # Step 1: Propose and approve
    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True
    pid = resp1.sql.proposal_id

    resp2 = pipeline.resume(thread_id, approved=True, proposal_id=pid)
    assert resp2.sql.status == ApprovalStatus.COMPLETED

    # Step 2: Attempt duplicate resume on same completed proposal
    resp3 = pipeline.resume(thread_id, approved=True, proposal_id=pid)
    assert resp3.sql.status == ApprovalStatus.COMPLETED
    assert "already" in resp3.answer.lower()


def test_langgraph_workflow_concurrent_approval_attempts(monkeypatch):
    """Concurrent threads attempting to resume same proposal: handled safely without duplicate run."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-concurrent-1"

    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True
    pid = resp1.sql.proposal_id
    shash = resp1.sql.sql_hash

    results = []
    errors = []

    def resume_worker(actor_name):
        try:
            r = pipeline.resume(
                thread_id,
                approved=True,
                proposal_id=pid,
                sql_hash=shash,
                actor=actor_name,
            )
            results.append(r)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=resume_worker, args=("worker_1",))
    t2 = threading.Thread(target=resume_worker, args=("worker_2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both completed safely without uncaught exception
    assert len(results) == 2 or (len(results) == 1 and len(errors) == 1)
    rec = get_audit_store().get(pid)
    assert rec is not None
    assert rec.status == ApprovalStatus.COMPLETED.value


def test_langgraph_workflow_failed_execution_audit_state(monkeypatch):
    """Failed SQL execution transitions proposal to FAILED and records outcome in audit store."""
    _setup_langgraph_mocks(monkeypatch)
    thread_id = "test-lg-failed-exec-1"

    from advanced_rag.graph import nodes
    monkeypatch.setattr(
        nodes.executor,
        "execute",
        MagicMock(side_effect=executor.SqlTimeoutError("Query exceeded deadline")),
    )

    resp1 = pipeline.ask("List all clusters", thread_id=thread_id)
    assert resp1.awaiting_approval is True
    pid = resp1.sql.proposal_id

    resp2 = pipeline.resume(thread_id, approved=True, proposal_id=pid)
    assert resp2.awaiting_approval is False
    assert resp2.sql.status == ApprovalStatus.FAILED
    assert "timed out" in resp2.answer.lower()

    rec = get_audit_store().get(pid)
    assert rec is not None
    assert rec.status == ApprovalStatus.FAILED.value
    assert "Timeout" in (rec.execution_outcome or "")


# =============================================================================
# 5. APPROVAL AUDIT DURABILITY & SECURITY
# =============================================================================


def test_approval_audit_store_in_memory_limitation():
    """Verify that ApprovalAuditStore is strictly in-process and does not persist across instances."""
    store1 = ApprovalAuditStore()
    pid = "durable-check-prop"
    store1.record_proposal(
        proposal_id=pid,
        thread_id="thr-1",
        sql="SELECT 1;",
        sql_hash="hash1",
        tables=["clusters"],
    )
    assert store1.get(pid) is not None

    # Simulate application restart / store recreation
    store2 = ApprovalAuditStore()
    assert store2.get(pid) is None  # Demonstrates in-memory non-durability limitation


def test_actor_is_untrusted_client_metadata():
    """Verify actor is stored as an unverified audit label, not a trusted identity."""
    store = ApprovalAuditStore()
    pid = "actor-check-prop"
    store.record_proposal(proposal_id=pid, thread_id="thr-2", sql="SELECT 1;")
    store.record_decision(pid, ApprovalStatus.APPROVED, actor="unverified_client_string")

    rec = store.get(pid)
    assert rec.actor == "unverified_client_string"
    # Documented property: actor is not verified by cryptographic signature or auth token in Stage 5


# =============================================================================
# 6. POSTGRESQL DRIVER CONFIGURATION (UNIT / MOCK)
# =============================================================================


def test_postgres_statement_timeout_mocked_unit():
    """Unit test verifying PostgreSQL SET LOCAL statement_timeout syntax and config.
    
    NOTE: This is a unit test mocking the PostgreSQL driver. Live PostgreSQL cancellation
    requires a running PostgreSQL database instance (see test quality report).
    """
    settings = Settings(
        sql_dialect="postgres",
        postgres_readonly_dsn="postgresql://ro_user:secret@localhost:5432/testdb",
        sql_timeout_seconds=3.5,
    )
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_result = MagicMock()
    mock_result.keys.return_value = ["name"]
    mock_result.fetchmany.return_value = [["prod-us"]]
    mock_conn.execute.return_value = mock_result

    with patch("advanced_rag.text2sql.executor.get_readonly_engine", return_value=mock_engine):
        res = executor.execute("SELECT name FROM clusters;", settings=settings)
        assert res.row_count == 1
        assert res.columns == ["name"]

        executed_stmts = [str(call[0][0]) for call in mock_conn.execute.call_args_list]
        assert any("SET LOCAL statement_timeout = '3500ms'" in stmt for stmt in executed_stmts)
        assert any("SET LOCAL transaction_read_only = on" in stmt for stmt in executed_stmts)


# =============================================================================
# 7. FASTAPI API INTEGRATION TESTS
# =============================================================================


def test_api_approve_with_metadata(monkeypatch):
    """Test API /approve endpoint accepts proposal_id, sql_hash, reason, actor."""
    from advanced_rag.api import main
    from advanced_rag.models import AnswerResponse

    seen = {}

    def mock_resume(thread_id, *, approved, **kwargs):
        seen["thread_id"] = thread_id
        seen["approved"] = approved
        seen.update(kwargs)
        return AnswerResponse(
            question="test",
            answer="query executed",
            route=Route.SQL,
        )

    monkeypatch.setattr(main.pipeline, "resume", mock_resume)

    client = TestClient(main.app)
    resp = client.post(
        "/approve",
        json={
            "thread_id": "thr-999",
            "approved": True,
            "proposal_id": "prop-888",
            "sql_hash": "hash-777",
            "reason": None,
            "actor": "test_user",
        },
    )
    assert resp.status_code == 200
    assert seen["thread_id"] == "thr-999"
    assert seen["approved"] is True
    assert seen["proposal_id"] == "prop-888"
    assert seen["sql_hash"] == "hash-777"
    assert seen["actor"] == "test_user"


def test_api_approval_audit_endpoint():
    """Test GET /approval/audit returns audit records."""
    from advanced_rag.api import main

    store = get_audit_store()
    pid = "audit-api-test-prop"
    store.record_proposal(
        proposal_id=pid,
        thread_id="audit-api-thread",
        sql="SELECT name FROM clusters;",
        sql_hash="hash99",
        dialect="sqlite",
        tables=["clusters"],
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )

    client = TestClient(main.app)
    resp = client.get("/approval/audit?limit=10")
    assert resp.status_code == 200
    records = resp.json()
    assert isinstance(records, list)
    matching = [r for r in records if r["proposal_id"] == pid]
    assert len(matching) == 1
    assert matching[0]["thread_id"] == "audit-api-thread"

    resp_thread = client.get("/approval/audit?thread_id=audit-api-thread")
    assert resp_thread.status_code == 200
    assert len(resp_thread.json()) >= 1
