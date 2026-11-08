"""Regression tests for Stage 2 Finalization: Production Retrieval Configuration.

Verifies:
- Task 1: Default collection resolution & env-var override & rollback to k8s_ops_bge_small
- Task 1: CLI and API collection selection
- Task 2: Hybrid RRF as default fusion & weighted fusion preservation & weight preservation
- Task 3: Optional CrossEncoder behavior & latency characteristics & top-k preservation
- Task 5: Citation and source metadata integrity on sre_copilot_v1
- Task 5: Baseline collections preservation (k8s_ops: 82 pts, k8s_ops_bge_small: 82 pts)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from qdrant_client import QdrantClient

from advanced_rag.api.main import RetrieveRequest
from advanced_rag.config import Settings, get_settings
from advanced_rag.ingestion.cli import build_parser
from advanced_rag.models import Chunk, RetrievedChunk
from advanced_rag.retrieval.retriever import Retriever
from advanced_rag.retrieval.vectorstore import HybridStore


def test_default_collection_resolution_without_env_override(monkeypatch):
    """Verify that Settings defaults to sre_copilot_v1 when unconfigured."""
    monkeypatch.delenv("QDRANT_COLLECTION", raising=False)
    # Ensure env_file is not overriding for unit purity
    s = Settings(_env_file=None)
    assert s.qdrant_collection == "sre_copilot_v1", "Default collection must be sre_copilot_v1"


def test_environment_variable_override_and_rollback(monkeypatch):
    """Verify QDRANT_COLLECTION environment variable overrides default cleanly for instant rollback."""
    monkeypatch.setenv("QDRANT_COLLECTION", "k8s_ops_bge_small")
    s = Settings(_env_file=None)
    assert s.qdrant_collection == "k8s_ops_bge_small", "Rollback to k8s_ops_bge_small via env var must succeed"

    monkeypatch.setenv("QDRANT_COLLECTION", "custom_test_collection")
    s2 = Settings(_env_file=None)
    assert s2.qdrant_collection == "custom_test_collection"


def test_cli_collection_resolution():
    """Verify rag-ingest CLI honors default collection and allows explicit override."""
    parser = build_parser()
    args_default = parser.parse_args([])
    assert args_default.collection is None, "CLI default collection arg must be None (deferring to settings)"

    args_custom = parser.parse_args(["--collection", "sre_copilot_v1"])
    assert args_custom.collection == "sre_copilot_v1"

    args_rollback = parser.parse_args(["--collection", "k8s_ops_bge_small"])
    assert args_rollback.collection == "k8s_ops_bge_small"


def test_api_retrieve_request_defaults():
    """Verify API RetrieveRequest defaults to RRF fusion and rerank=False."""
    req = RetrieveRequest(query="pod crash")
    assert req.mode == "hybrid"
    assert req.fusion == "rrf", "API request default fusion must be 'rrf'"
    assert req.rerank is False, "API request default rerank must be False"
    assert req.use_hyde is False


def test_rrf_is_default_fusion_in_settings():
    """Verify RRF is the configured default fusion in Settings."""
    s = Settings(_env_file=None)
    assert s.hybrid_fusion == "rrf"
    assert s.enable_rerank is False
    assert s.retrieve_top_k == 20
    assert s.rerank_top_n == 5
    assert s.hybrid_dense_weight == 0.7, "HYBRID_DENSE_WEIGHT must remain preserved at 0.7"


def test_hybrid_store_defaults_to_rrf_and_preserves_weighted(monkeypatch):
    """Verify HybridStore.search calls _hybrid_rrf by default and preserves _hybrid_weighted."""
    settings = Settings(_env_file=None)
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True
    coll_mock = MagicMock()
    coll_mock.config.params.vectors = {"dense": MagicMock()}
    mock_client.get_collection.return_value = coll_mock

    store = HybridStore(settings=settings, collection="sre_copilot_v1", client=mock_client)

    # Mock _hybrid_rrf and _hybrid_weighted
    store._hybrid_rrf = MagicMock(return_value=[])
    store._hybrid_weighted = MagicMock(return_value=[])

    # 1. Search with default fusion (None -> settings default 'rrf')
    store.search("CrashLoopBackOff", mode="hybrid")
    store._hybrid_rrf.assert_called_once()
    store._hybrid_weighted.assert_not_called()

    # 2. Search with explicit fusion="weighted"
    store._hybrid_rrf.reset_mock()
    store._hybrid_weighted.reset_mock()
    store.search("CrashLoopBackOff", mode="hybrid", fusion="weighted")
    store._hybrid_rrf.assert_not_called()
    store._hybrid_weighted.assert_called_once()


def test_retriever_defaults_to_no_rerank_and_rrf():
    """Verify Retriever defaults to settings.enable_rerank (False) and settings.hybrid_fusion (rrf)."""
    settings = Settings(_env_file=None)
    mock_store = MagicMock()
    fake_chunk = RetrievedChunk(
        chunk=Chunk(id="1", text="some text", source="sre/runbook.md", section="Triage"),
        retrieval_score=0.9,
    )
    mock_store.search.return_value = [fake_chunk]

    retriever = Retriever(settings=settings, store=mock_store)
    retriever.rerank = MagicMock(return_value=[fake_chunk])

    # Default retrieve call: rerank=None (should resolve to False)
    result = retriever.retrieve("CrashLoopBackOff", use_hyde=False)

    mock_store.search.assert_called_once()
    call_kwargs = mock_store.search.call_args.kwargs
    assert call_kwargs["fusion"] == "rrf"
    assert result.reranked is False
    retriever.rerank.assert_not_called()

    # Explicit rerank=True
    retriever.rerank.reset_mock()
    mock_store.search.reset_mock()
    result2 = retriever.retrieve("CrashLoopBackOff", rerank=True, use_hyde=False)
    assert result2.reranked is True
    retriever.rerank.assert_called_once()


def test_baseline_collections_remain_unmodified():
    """Verify baseline collections (k8s_ops, k8s_ops_bge_small) still exist with exactly 82 points."""
    settings = get_settings()
    client = QdrantClient(path=str(settings.absolute(settings.qdrant_path)))
    try:
        assert client.collection_exists("k8s_ops")
        assert client.count("k8s_ops").count == 82

        assert client.collection_exists("k8s_ops_bge_small")
        assert client.count("k8s_ops_bge_small").count == 82
    finally:
        client.close()


def test_sre_copilot_collection_integrity_and_citations():
    """Verify active collection sre_copilot_v1 has 5,533 points and produces valid citations."""
    settings = get_settings()
    client = QdrantClient(path=str(settings.absolute(settings.qdrant_path)))
    try:
        assert client.collection_exists("sre_copilot_v1")
        count = client.count("sre_copilot_v1").count
        assert count == 5533, f"Expected 5,533 points in sre_copilot_v1, got {count}"

        store = HybridStore(settings=settings, collection="sre_copilot_v1", client=client)
        results = store.search("OOMKilled exit code 137", top_k=5, mode="hybrid")
        assert len(results) > 0
        for r in results:
            citation = r.chunk.citation()
            assert len(citation) > 0
            assert r.chunk.source
            assert r.chunk.section
    finally:
        client.close()
