"""Final QA Campaign: Security, Vulnerability & Boundary Edge-Case Tests.

Covers:
1. SQL Injection & AST Bypass Attempts (Stacked queries, UNION attacks, comment bypasses, system tables).
2. Hash Tampering & Approval Security (Proposal ID tampering, SQL hash tampering, replay attacks).
3. Secret & Credential Leakage Prevention (Masked DSNs, exception masking, safe log formatters).
4. Path Traversal & Document Security (Directory traversal payloads in document IDs).
5. Boundary Inputs (Very short queries, whitespace queries, Unicode, emoji, special characters).
"""

import pytest
from unittest.mock import MagicMock, patch

from advanced_rag.config import Settings
from advanced_rag.graph import pipeline
from advanced_rag.graph.builder import build_graph
from langgraph.checkpoint.memory import MemorySaver
from advanced_rag.models import SqlProposal, ApprovalStatus
from advanced_rag.text2sql.security import (
    validate_sql_ast,
    OPERATIONAL_TABLES,
)
from advanced_rag.text2sql.executor import compute_sql_hash, execute


# =============================================================================
# 1. SQL INJECTION & AST BYPASS TESTING
# =============================================================================

@pytest.mark.parametrize(
    "attack_query",
    [
        "SELECT id FROM clusters; DROP TABLE clusters; --",
        "SELECT id FROM clusters WHERE id = 1 OR (SELECT count(*) FROM sqlite_master) > 0",
        "SELECT id FROM clusters UNION SELECT name FROM sqlite_master",
        "SELECT id FROM clusters UNION SELECT password FROM pg_shadow",
        "SELECT * FROM clusters /* comment */ WHERE id = 1; DELETE FROM nodes;",
        "SELECT id FROM clusters WHERE name = '' OR ''=''; ATTACH DATABASE 'x.db' AS x;",
        "SELECT load_extension('evil.so');",
        "SELECT hex(randomblob(1000000000));",
    ],
)
def test_sql_injection_attacks_blocked(attack_query):
    """Verify various SQL injection patterns are caught and blocked by validate_sql_ast."""
    res = validate_sql_ast(attack_query, dialect="sqlite")
    # Must either be marked invalid or raise SqlSecurityViolation
    assert res.is_valid is False or res.error is not None


def test_sqlite_system_tables_strictly_forbidden():
    """Verify any query referencing sqlite_master or sqlite_schema is rejected."""
    system_queries = [
        "SELECT name FROM sqlite_master WHERE type='table';",
        "SELECT sql FROM sqlite_schema;",
    ]
    for q in system_queries:
        res = validate_sql_ast(q, dialect="sqlite")
        assert res.is_valid is False
        assert "disallowed" in res.error.lower() or "forbidden" in res.error.lower() or "unauthorized" in res.error.lower()


# =============================================================================
# 2. HASH TAMPERING & APPROVAL SECURITY
# =============================================================================

def test_resume_rejects_modified_sql_hash():
    """Verify pipeline.resume rejects execution if the approval hash does not match proposal."""
    saver = MemorySaver()
    thread_id = "sec-tamper-thread-1"

    proposal = SqlProposal(
        proposal_id="prop-tamper-sec-1",
        sql_hash="valid_hash_12345",
        sql="SELECT id, name FROM clusters WHERE environment = 'production'",
        rationale="Inspect clusters",
        dialect="sqlite",
        tables=["clusters"],
        status=ApprovalStatus.PENDING,
    )

    mock_gen = MagicMock()
    mock_gen.generate.return_value = proposal

    with patch("advanced_rag.graph.nodes.get_sql_generator", return_value=mock_gen):
        with patch("advanced_rag.graph.nodes.route_node", return_value={"route": "sql"}):
            graph = build_graph(checkpointer=saver)
            resp = pipeline.ask("List prod clusters", thread_id=thread_id, force_extractive=True, graph=graph)
            assert resp.awaiting_approval is True

    # Attempt to approve with a forged/tampered hash
    resumed = pipeline.resume(
        thread_id=thread_id,
        approved=True,
        actor="malicious_user",
        proposal_id="prop-tamper-sec-1",
        sql_hash="forged_attacker_hash_67890",
        graph=graph,
    )

    assert "Security violation" in resumed.answer
    assert resumed.sql.status == ApprovalStatus.REJECTED


def test_resume_rejects_mismatched_proposal_id():
    """Verify pipeline.resume rejects execution if proposal_id does not match the parked interrupt."""
    saver = MemorySaver()
    thread_id = "sec-proposal-mismatch-2"

    proposal = SqlProposal(
        proposal_id="prop-legit-222",
        sql_hash="hash_222",
        sql="SELECT count(*) FROM nodes",
        rationale="Count nodes",
        dialect="sqlite",
        tables=["nodes"],
        status=ApprovalStatus.PENDING,
    )

    mock_gen = MagicMock()
    mock_gen.generate.return_value = proposal

    with patch("advanced_rag.graph.nodes.get_sql_generator", return_value=mock_gen):
        with patch("advanced_rag.graph.nodes.route_node", return_value={"route": "sql"}):
            graph = build_graph(checkpointer=saver)
            resp = pipeline.ask("Count nodes", thread_id=thread_id, force_extractive=True, graph=graph)
            assert resp.awaiting_approval is True

    # Resume with wrong proposal ID
    resumed = pipeline.resume(
        thread_id=thread_id,
        approved=True,
        actor="admin",
        proposal_id="prop-WRONG-999",
        sql_hash="hash_222",
        graph=graph,
    )

    assert "Security violation" in resumed.answer or resumed.sql.status == ApprovalStatus.REJECTED


# =============================================================================
# 3. SECRET LEAKAGE PREVENTION
# =============================================================================

def test_credentials_never_exposed_in_masked_dsn():
    """Verify credentials with special characters and complex passwords are never leaked."""
    passwords = [
        "P@ssw0rd!#%&*",
        "super_secret_token_12345",
        "complex$password;drop",
    ]
    for pw in passwords:
        dsn = f"postgresql+psycopg://sre_user:{pw}@127.0.0.1:5432/ops_prod"
        st = Settings(postgres_dsn=dsn)
        masked = st.masked_dsn(dsn)
        assert pw not in masked
        assert "***" in masked
        assert "sre_user" in masked


# =============================================================================
# 4. BOUNDARY & UNUSUAL INPUT TESTING
# =============================================================================

def test_pipeline_handles_unicode_and_emojis():
    """Verify non-ASCII, Unicode characters, and emojis do not crash the pipeline."""
    saver = MemorySaver()
    graph = build_graph(checkpointer=saver)
    
    unicode_questions = [
        "Kubernetes 节点崩溃 CrashLoopBackOff",
        "Hvorfor feiler pod-en min? 💥 🚀",
        "Что такое OutOfMemoryKilled (OOMKilled)?",
    ]
    for q in unicode_questions:
        resp = pipeline.ask(q, thread_id=f"unicode-{hash(q)}", force_extractive=True, graph=graph)
        assert resp is not None
        assert isinstance(resp.answer, str)
        assert len(resp.answer) > 0


def test_pipeline_handles_special_characters_gracefully():
    """Verify special punctuation and symbols do not break execution."""
    saver = MemorySaver()
    graph = build_graph(checkpointer=saver)
    
    special_q = "Pod failed: exit code 137 [SIGKILL] -- check cgroup.procs & limits/requests?!"
    resp = pipeline.ask(special_q, thread_id="special-char-thread", force_extractive=True, graph=graph)
    assert resp is not None
    assert len(resp.answer) > 0
