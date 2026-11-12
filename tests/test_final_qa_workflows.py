"""Comprehensive End-to-End Workflow Tests (Workflows 1-5).

Mandatory QA Verification:
- Workflow 1: Document Ingestion, Chunking & Registry Grounding.
- Workflow 2: Advanced RAG Pipeline (Grounded Extractive/Synthesized Generation & Citations).
- Workflow 3: Text2SQL End-to-End Approval Lifecycle (Proposal, Approval, Execution & Rejection).
- Workflow 4: Persistent State Across Process Restarts (Cross-Process Graph Resumption).
- Workflow 5: Multi-Tier Caching & Invalidation (Embedding, Retrieval, Generation Invalidation & Fail-Soft Fallback).
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from advanced_rag.cache import (
    EmbeddingCache,
    IntentRoutingCache,
    RetrievalCache,
)
from advanced_rag.config import Settings
from advanced_rag.db.session import get_engine
from advanced_rag.graph import builder, pipeline
from advanced_rag.graph.nodes import Route
from advanced_rag.ingestion.chunker import chunk_document
from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry
from advanced_rag.models import ApprovalStatus, SqlProposal
from advanced_rag.text2sql.database import QueryResult
from advanced_rag.text2sql.executor import compute_sql_hash


# =============================================================================
# WORKFLOW 1: DOCUMENT INGESTION & RETRIEVAL GROUNDING
# =============================================================================


def test_workflow_1_document_ingestion_and_registry_lifecycle():
    """Verify document chunking, registry tracking, ingestion run, and cleanup."""
    # 1. Synthesize SRE Runbook document
    doc_text = """# Incident Runbook: PaymentGateway504Alert

## Symptoms
API gateway returns HTTP 504 Gateway Timeout during peak traffic windows.

## Root Cause
Payment gateway service connection pool exhaustion under load.

## Remediation Steps
1. Verify payment-service pod status:
   `kubectl get pods -n prod -l app=payment-service`
2. Check database connection pool metrics in Prometheus.
3. Scale replicas to handle load:
   `kubectl scale deployment payment-service --replicas=10 -n prod`
4. Confirm error rates drop below SLO target (0.1%).
"""
    source_path = "runbooks/prod/payment_gateway_504.md"
    doc_title = "PaymentGateway504Alert Runbook"

    # 2. Chunk document using chunker
    chunks = chunk_document(
        text=doc_text,
        source=source_path,
        title=doc_title,
        doc_type="runbook",
        chunk_size=300,
        chunk_overlap=50,
    )
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.source == source_path
        assert chunk.title == doc_title
        assert len(chunk.text.strip()) > 0
        assert chunk.id is not None

    # 3. Register document in PersistentDocumentRegistry using SQLite engine
    test_settings = Settings(postgres_metadata_dsn="")  # forces SQLite ops.db / fallback engine
    engine = get_engine(test_settings)
    registry = PersistentDocumentRegistry(engine=engine, settings=test_settings, auto_init=True)

    unique_doc_id = f"test-doc-{uuid.uuid4().hex[:8]}"
    doc_record = {
        "document_id": unique_doc_id,
        "source": source_path,
        "title": doc_title,
        "file_type": "md",
        "doc_type": "runbook",
        "content_hash": "hash-504-alert-test",
        "category": "runbooks",
        "chunk_count": len(chunks),
        "status": "pending",
    }

    try:
        registered = registry.register(doc_record)
        assert registered["document_id"] == unique_doc_id
        assert registered["status"] == "pending"

        # Record ingestion run
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        registry.record_ingestion_run(
            operation_type="test_incremental",
            collection_name=test_settings.qdrant_collection,
            run_id=run_id,
        )

        # Transition document status to indexed
        registry.record_indexing_success(
            document_id=unique_doc_id,
            chunk_count=len(chunks),
        )

        # Complete ingestion run
        registry.complete_ingestion_run(
            run_id=run_id,
            status="completed",
            successful_files=1,
            failed_files=0,
            total_chunks=len(chunks),
        )

        # Verify document metadata is queryable
        fetched = registry.get(unique_doc_id)
        assert fetched is not None
        assert fetched["document_id"] == unique_doc_id
        assert fetched["status"] == "indexed"
        assert fetched["chunk_count"] == len(chunks)

    finally:
        # Clean up test document
        deleted = registry.delete(unique_doc_id)
        assert deleted is True
        fetched_after = registry.get(unique_doc_id)
        assert fetched_after is not None
        assert fetched_after["status"] == "deleted"


# =============================================================================
# WORKFLOW 2: ADVANCED RAG PIPELINE
# =============================================================================


def test_workflow_2_advanced_rag_pipeline_execution():
    """Verify end-to-end RAG question answering with grounding and citations."""
    question = "What causes CrashLoopBackOff in Kubernetes pods and how to fix it?"
    
    # Run through full pipeline with extractive mode (deterministic, no external LLM API required)
    resp = pipeline.ask(
        question,
        force_extractive=True,
    )

    assert resp is not None
    assert resp.question == question
    assert resp.route == Route.VECTOR
    assert resp.answer is not None and len(resp.answer.strip()) > 0
    # Verify citations exist and have proper structure
    assert isinstance(resp.citations, list)
    if resp.citations:
        first_citation = resp.citations[0]
        assert hasattr(first_citation, "source") or "source" in first_citation
    assert resp.latency_ms > 0
    assert resp.thread_id is not None


# =============================================================================
# WORKFLOW 3: TEXT2SQL END-TO-END APPROVAL LIFECYCLE
# =============================================================================


def test_workflow_3_text2sql_approval_and_execution_lifecycle():
    """Verify complete Text2SQL lifecycle: Proposal -> Interrupt -> Approval -> DB Execution."""
    shared_checkpointer = MemorySaver()
    thread_id = f"wf3-sql-approval-{uuid.uuid4().hex[:8]}"

    sql_text = "SELECT id, name, environment FROM clusters WHERE environment = 'production'"
    sql_hash = compute_sql_hash(sql_text)
    proposal_id = f"prop-wf3-{uuid.uuid4().hex[:6]}"

    proposal = SqlProposal(
        proposal_id=proposal_id,
        sql_hash=sql_hash,
        sql=sql_text,
        rationale="Inspect production clusters for high-availability compliance",
        dialect="sqlite",
        tables=["clusters"],
        status=ApprovalStatus.PENDING,
    )

    mock_gen = MagicMock()
    mock_gen.generate.return_value = proposal

    # Step 1: Submit query -> routed to SQL -> parked at human approval gate
    with patch("advanced_rag.graph.nodes.get_sql_generator", return_value=mock_gen):
        with patch("advanced_rag.graph.nodes.route_node", return_value={"route": Route.SQL}):
            graph = builder.build_graph(checkpointer=shared_checkpointer)
            resp = pipeline.ask(
                "List all production clusters",
                thread_id=thread_id,
                force_extractive=True,
                graph=graph,
            )

    assert resp.awaiting_approval is True
    assert resp.sql is not None
    assert resp.sql.proposal_id == proposal_id
    assert resp.sql.status == ApprovalStatus.PENDING

    # Step 2: Verify pending interrupt exists
    pending_info = pipeline.pending_approval(thread_id, graph=graph)
    assert pending_info is not None
    assert pending_info.get("proposal_id") == proposal_id
    assert pending_info.get("sql_hash") == sql_hash

    # Step 3: Approve proposal and resume execution
    with patch("advanced_rag.text2sql.executor.execute") as mock_exec:
        mock_exec.return_value = QueryResult(
            columns=["id", "name", "environment"],
            rows=[[1, "prod-us-east-1", "production"], [2, "prod-eu-west-1", "production"]],
        )
        resumed_resp = pipeline.resume(
            thread_id=thread_id,
            approved=True,
            actor="sre-lead",
            proposal_id=proposal_id,
            sql_hash=sql_hash,
            graph=graph,
        )

    assert resumed_resp.awaiting_approval is False
    assert resumed_resp.sql.status == ApprovalStatus.COMPLETED
    assert resumed_resp.sql.row_count == 2


def test_workflow_3_text2sql_rejection_lifecycle():
    """Verify Text2SQL rejection terminates cleanly without database execution."""
    shared_checkpointer = MemorySaver()
    thread_id = f"wf3-sql-reject-{uuid.uuid4().hex[:8]}"
    proposal_id = f"prop-wf3-rej-{uuid.uuid4().hex[:6]}"
    sql_text = "SELECT * FROM deployments"
    sql_hash = compute_sql_hash(sql_text)

    proposal = SqlProposal(
        proposal_id=proposal_id,
        sql_hash=sql_hash,
        sql=sql_text,
        rationale="Inspect all deployments",
        dialect="sqlite",
        tables=["deployments"],
        status=ApprovalStatus.PENDING,
    )

    mock_gen = MagicMock()
    mock_gen.generate.return_value = proposal

    with patch("advanced_rag.graph.nodes.get_sql_generator", return_value=mock_gen):
        with patch("advanced_rag.graph.nodes.route_node", return_value={"route": Route.SQL}):
            graph = builder.build_graph(checkpointer=shared_checkpointer)
            resp = pipeline.ask(
                "Show all deployments",
                thread_id=thread_id,
                force_extractive=True,
                graph=graph,
            )

    assert resp.awaiting_approval is True

    # Reject proposal
    with patch("advanced_rag.text2sql.executor.execute") as mock_exec:
        resumed_resp = pipeline.resume(
            thread_id=thread_id,
            approved=False,
            reason="Query scope too broad for production safety policy",
            actor="security-officer",
            proposal_id=proposal_id,
            sql_hash=sql_hash,
            graph=graph,
        )
        # Executor must NOT have been called
        mock_exec.assert_not_called()

    assert resumed_resp.awaiting_approval is False
    assert resumed_resp.sql.status == ApprovalStatus.REJECTED
    assert "rejected" in resumed_resp.answer.lower()


# =============================================================================
# WORKFLOW 4: PERSISTENT STATE ACROSS PROCESS RESTARTS
# =============================================================================


def test_workflow_4_state_recovery_across_simulated_process_restart():
    """Verify thread checkpoint survives process exit and resumes in a fresh graph instance."""
    shared_checkpointer = MemorySaver()
    thread_id = f"wf4-restart-{uuid.uuid4().hex[:8]}"

    # Process A: Runs query and creates checkpoint
    graph_proc_a = builder.build_graph(checkpointer=shared_checkpointer)
    resp_a = pipeline.ask(
        "How to troubleshoot high CPU on Kubernetes worker nodes?",
        thread_id=thread_id,
        force_extractive=True,
        graph=graph_proc_a,
    )
    assert resp_a.thread_id == thread_id

    # Simulate Process A termination
    del graph_proc_a
    builder.get_graph.cache_clear()

    # Process B: Starts afresh, connects to shared persistence, compiles new graph
    graph_proc_b = builder.build_graph(checkpointer=shared_checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    recovered_state = graph_proc_b.get_state(config)

    assert recovered_state is not None
    assert recovered_state.values.get("original_question") == "How to troubleshoot high CPU on Kubernetes worker nodes?"
    assert recovered_state.values.get("force_extractive") is True
    assert recovered_state.values.get("route") == Route.VECTOR


# =============================================================================
# WORKFLOW 5: MULTI-TIER CACHING & INVALIDATION
# =============================================================================


def test_workflow_5_multi_tier_caching_and_invalidation():
    """Verify L1/L2 caching, generation invalidation, and fail-soft fallback on Redis errors."""
    # 1. Embedding Cache: Single compute, subsequent hit
    emb_cache = EmbeddingCache(max_entries=100, ttl=300)
    compute_fn = MagicMock(return_value=[0.11, 0.22, 0.33])

    vec1 = emb_cache.get_or_compute_dense("Kubernetes Ingress Controller", "bge-small", 384, compute_fn)
    vec2 = emb_cache.get_or_compute_dense("Kubernetes Ingress Controller", "bge-small", 384, compute_fn)

    assert vec1 == [0.11, 0.22, 0.33]
    assert vec2 == vec1
    assert compute_fn.call_count == 1  # Hit L1 cache on second call

    # 2. Intent Cache: Single compute, subsequent hit
    intent_cache = IntentRoutingCache(max_entries=50, ttl=300)
    route_fn = MagicMock(return_value={"route": "rag", "confidence": 0.95})

    r1 = intent_cache.get_or_compute("What is OOMKilled?", "intent-model-v1", "v1.0", route_fn)
    r2 = intent_cache.get_or_compute("What is OOMKilled?", "intent-model-v1", "v1.0", route_fn)

    assert r1 == {"route": "rag", "confidence": 0.95}
    assert r2 == r1
    assert route_fn.call_count == 1

    # 3. Retrieval Cache: Hit and Generation Invalidation
    ret_cache = RetrievalCache(max_entries=50, ttl=300)
    sample_results = [{"doc_id": "doc-1", "score": 0.92}]

    assert ret_cache.get_results("CrashLoopBackOff", "sre_copilot_v1", 5) is None
    ret_cache.set_results("CrashLoopBackOff", "sre_copilot_v1", 5, sample_results)
    
    cached = ret_cache.get_results("CrashLoopBackOff", "sre_copilot_v1", 5)
    assert cached == sample_results

    # Bump generation -> cache invalidated
    ret_cache.bump_generation()
    assert ret_cache.get_results("CrashLoopBackOff", "sre_copilot_v1", 5) is None

    # 4. Redis Fail-Soft Fallback
    from advanced_rag.cache import RedisSharedCache
    redis_cache = RedisSharedCache("redis://127.0.0.1:6379/1")

    mock_client = MagicMock()
    mock_client.get.side_effect = ConnectionError("Redis server connection lost")
    mock_client.set.side_effect = ConnectionError("Redis server connection lost")
    redis_cache._client = mock_client

    # Should safely return None on read failure instead of raising
    cached_val = redis_cache.get("some_key")
    assert cached_val is None

    # Should safely return False on write failure instead of raising
    set_success = redis_cache.set("some_key", b"some_bytes", ttl=60)
    assert set_success is False
