"""Tests for Stage 4 - Batch 2: Task 47 (Retrieval Quality and Context Optimization).

Covers:
1. Batch 1 carry-forward prefetch guard clamping (prefetch_limit >= top_k).
2. HyDE generation on valid queries, skipping for short/empty queries, failure fallbacks.
3. CrossEncoder reranker error-resilience and graceful ranking fallback.
4. Metadata filter builder (category, doc_type, source_prefix, document_id, source_version).
5. Context selection: relevance thresholding, sliding-window deduplication, token budgeting.
6. 1-to-1 citation alignment between context_chunks and citations_from.
7. Retrieval API and parameter acceptance.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from advanced_rag.config import Settings
from advanced_rag.models import Chunk, RetrievedChunk
from advanced_rag.retrieval.retriever import (
    Retriever,
    format_context,
    select_context_chunks,
)
from advanced_rag.retrieval.vectorstore import HybridStore, build_filter
from advanced_rag.graph.nodes import citations_from


def make_chunk(
    chunk_id: str,
    text: str,
    source: str = "k8s/test.md",
    section: str = "S1",
    score: float = 0.8,
    doc_type: str = "runbook",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            id=chunk_id,
            text=text,
            source=source,
            section=section,
            doc_type=doc_type,
        ),
        retrieval_score=score,
    )


# -----------------------------------------------------------------------------
# 1. Batch 1 Carry-Forward Guard: prefetch_limit >= top_k
# -----------------------------------------------------------------------------


def test_prefetch_guard_clamps_manual_override():
    settings = Settings(hybrid_prefetch_limit=5)
    store = HybridStore.__new__(HybridStore)
    store.settings = settings
    store.collection = "sre_copilot_v1"
    store._client = MagicMock()
    store._client.query_points.return_value = MagicMock(points=[])
    store._prefetch = MagicMock(return_value=[])

    # top_k=20 is larger than hybrid_prefetch_limit=5.
    # The guard should clamp prefetch_limit up to max(5, 20) = 20.
    store._hybrid_rrf("test query", top_k=20, query_filter=None)

    store._prefetch.assert_called_once_with("test query", 20)


# -----------------------------------------------------------------------------
# 2. HyDE: Validation, Short/Empty Skipping, Fallback Resilience
# -----------------------------------------------------------------------------


def test_hyde_skipped_for_empty_and_short_queries():
    retriever = Retriever(settings=Settings(enable_hyde=True))
    retriever._llm = MagicMock()

    assert retriever.generate_hyde("") is None
    assert retriever.generate_hyde("   ") is None
    assert retriever.generate_hyde("hi") is None
    retriever._llm.complete.assert_not_called()


def test_hyde_falls_back_when_llm_fails_or_refuses():
    retriever = Retriever(settings=Settings(enable_hyde=True))
    mock_llm = MagicMock()
    retriever._llm = mock_llm

    # LLM raises exception
    mock_llm.complete.side_effect = RuntimeError("API connection timeout")
    with patch("advanced_rag.retrieval.retriever.llm_available", return_value=True):
        assert retriever.generate_hyde("how to fix crashloopbackoff") is None

    # LLM returns refusal
    mock_llm.complete.side_effect = None
    mock_llm.complete.return_value = MagicMock(refused=True, text="")
    with patch("advanced_rag.retrieval.retriever.llm_available", return_value=True):
        assert retriever.generate_hyde("how to fix crashloopbackoff") is None


def test_retrieve_uses_raw_query_when_hyde_fails():
    store = MagicMock()
    store.search.return_value = [make_chunk("c1", "sample text")]
    retriever = Retriever(store=store, settings=Settings(enable_hyde=True, enable_rerank=False))
    retriever.generate_hyde = MagicMock(return_value=None)

    result = retriever.retrieve("pod crash 137", use_hyde=True)
    assert result.query_used == "pod crash 137"
    assert result.hyde_document is None
    store.search.assert_called_once()
    assert store.search.call_args.args[0] == "pod crash 137"


# -----------------------------------------------------------------------------
# 3. CrossEncoder: Resilience, Fallback, Latency
# -----------------------------------------------------------------------------


def test_crossencoder_failure_falls_back_to_retrieval_ranking():
    mock_reranker = MagicMock()
    mock_reranker.is_cross_encoder = True
    mock_reranker.score.side_effect = RuntimeError("ONNX inference failure")

    retriever = Retriever(reranker=mock_reranker, settings=Settings())
    chunks = [
        make_chunk("c1", "first chunk", score=0.9),
        make_chunk("c2", "second chunk", score=0.8),
    ]

    reranked = retriever.rerank("query", chunks, top_n=2)
    # Should safely return chunks in original retrieval order without crashing
    assert [c.chunk.id for c in reranked] == ["c1", "c2"]
    assert reranked[0].retrieval_score == 0.9


def test_rerank_disabled_preserves_retrieval_order_and_leaves_rerank_ms_none():
    store = MagicMock()
    store.search.return_value = [
        make_chunk("c1", "first chunk", score=0.9),
        make_chunk("c2", "second chunk", score=0.7),
    ]
    retriever = Retriever(store=store, settings=Settings(enable_rerank=False))
    retriever.rerank = MagicMock()

    result = retriever.retrieve("query", rerank=False)
    retriever.rerank.assert_not_called()
    assert result.reranked is False
    assert result.rerank_ms is None
    assert len(result.chunks) == 2


# -----------------------------------------------------------------------------
# 4. Metadata Filtering: build_filter
# -----------------------------------------------------------------------------


def test_build_filter_empty_or_whitespace_returns_none():
    assert build_filter() is None
    assert build_filter(category="", doc_type="  ", source_prefix="") is None


def test_build_filter_category_top_level_expands_to_should():
    f = build_filter(category="kubernetes")
    assert f is not None
    assert len(f.must) == 1
    # Check that it generated a nested should condition for category + source prefix
    nested = f.must[0]
    assert hasattr(nested, "should")
    assert len(nested.should) == 3


def test_build_filter_doc_type_and_source_prefix_and_document_id():
    f = build_filter(
        doc_type="runbook",
        source_prefix="helm/",
        document_id="doc123",
        source_version="1.31",
    )
    assert f is not None
    assert len(f.must) == 4
    keys = {c.key for c in f.must}
    assert keys == {"doc_type", "source", "document_id", "source_version"}


def test_build_filter_namespace_alias():
    f = build_filter(namespace="prometheus")
    assert f is not None
    assert len(f.must) == 1
    nested = f.must[0]
    assert hasattr(nested, "should")
    assert len(nested.should) == 3


# -----------------------------------------------------------------------------
# 5. Context Selection: Relevance Thresholding, Deduplication, Budgeting
# -----------------------------------------------------------------------------


def test_select_context_chunks_empty():
    assert select_context_chunks([]) == []


def test_select_context_chunks_relevance_thresholding():
    chunks = [
        make_chunk("c1", "high score", score=0.95),
        make_chunk("c2", "medium score", score=0.60),
        make_chunk("c3", "very low noise", score=0.10),
    ]
    # min_score_ratio=0.20 -> threshold is 0.95 * 0.20 = 0.19
    # c3 (0.10) should be dropped
    selected = select_context_chunks(chunks, min_score_ratio=0.20)
    assert [c.chunk.id for c in selected] == ["c1", "c2"]


def test_select_context_chunks_retains_at_least_one():
    chunks = [
        make_chunk("c1", "only chunk", score=0.05),
    ]
    # Even if score is very low, at least 1 candidate is preserved
    selected = select_context_chunks(chunks, min_score_ratio=0.90)
    assert len(selected) == 1
    assert selected[0].chunk.id == "c1"


def test_select_context_chunks_deduplicates_overlapping_window_chunks():
    # Two chunks with identical words from same source document
    text_a = "Kubernetes pods enter CrashLoopBackOff when the container terminates with non-zero exit code repeatedly."
    text_b = "Kubernetes pods enter CrashLoopBackOff when the container terminates with non-zero exit code repeatedly and restarts."
    chunks = [
        make_chunk("c1", text_a, source="k8s/pod.md", score=0.9),
        make_chunk("c2", text_b, source="k8s/pod.md", score=0.85),
        make_chunk("c3", "Prometheus alerts fire when crashloop metrics exceed threshold.", source="prom/alert.md", score=0.8),
    ]
    # c2 is a near duplicate of c1 (Jaccard > 0.75 from same source)
    selected = select_context_chunks(chunks, enable_deduplication=True)
    assert [c.chunk.id for c in selected] == ["c1", "c3"]


def test_select_context_chunks_token_budget_cutoff():
    # Long text: 800 chars ~= 200 tokens each, across distinct sources
    chunks = [
        make_chunk("c1", "alpha " * 160, source="k8s/a.md", score=0.9),
        make_chunk("c2", "beta " * 160, source="k8s/b.md", score=0.8),
        make_chunk("c3", "gamma " * 160, source="k8s/c.md", score=0.7),
        make_chunk("c4", "delta " * 160, source="k8s/d.md", score=0.6),
    ]
    # max_tokens=450: c1 (~200) + c2 (~200) fits (400 <= 450), c3 would exceed (600 > 450)
    selected = select_context_chunks(chunks, max_tokens=450)
    assert len(selected) == 2
    assert [c.chunk.id for c in selected] == ["c1", "c2"]


# -----------------------------------------------------------------------------
# 6. Citation Alignment: 1-to-1 Mapping
# -----------------------------------------------------------------------------


def test_citation_alignment_with_context_chunks():
    c1 = make_chunk("c1", "First context chunk", source="k8s/pod.md", section="Lifecycle")
    c2 = make_chunk("c2", "Second context chunk", source="helm/install.md", section="Upgrade")
    selected = [c1, c2]

    # format_context renders [1] and [2]
    context_str = format_context(selected)
    assert "[1] k8s/pod.md - Lifecycle" in context_str
    assert "[2] helm/install.md - Upgrade" in context_str

    # citations_from should return citations strictly corresponding to selected context_chunks
    state = {
        "context_chunks": selected,
        "chunks": [c1, c2, make_chunk("c3", "unused chunk", source="sre/other.md")],
        "extractive": False,
    }
    citations = citations_from(state)
    assert len(citations) == 2
    assert citations[0].source == "k8s/pod.md"
    assert citations[1].source == "helm/install.md"


# -----------------------------------------------------------------------------
# 7. API /retrieve endpoint accepts category and filter fields
# -----------------------------------------------------------------------------


def test_retrieve_api_accepts_category_and_filters(monkeypatch):
    from fastapi.testclient import TestClient
    from advanced_rag.api import main

    monkeypatch.setattr(main.pipeline, "get_graph", lambda: None)
    mock_retriever = MagicMock()
    mock_retriever.retrieve.return_value = MagicMock(
        chunks=[make_chunk("c1", "mock text")],
        hyde_document=None,
        reranked=False,
        cross_encoder=False,
        elapsed_ms=12.3,
        hyde_ms=None,
        rerank_ms=None,
    )

    with patch("advanced_rag.retrieval.get_retriever", return_value=mock_retriever):
        with TestClient(main.app) as client:
            resp = client.post(
                "/retrieve",
                json={
                    "query": "crashloopbackoff",
                    "mode": "hybrid",
                    "fusion": "rrf",
                    "category": "kubernetes",
                    "doc_type": "runbook",
                    "source_prefix": "kubernetes/",
                    "document_id": "doc_123",
                    "source_version": "1.31",
                    "namespace": "kubernetes",
                    "top_k": 10,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["category"] == "kubernetes"
            assert data["candidate_count"] == 10
            mock_retriever.retrieve.assert_called_once()
            call_kwargs = mock_retriever.retrieve.call_args.kwargs
            assert call_kwargs["category"] == "kubernetes"
            assert call_kwargs["doc_type"] == "runbook"
            assert call_kwargs["source_prefix"] == "kubernetes/"
            assert call_kwargs["document_id"] == "doc_123"
            assert call_kwargs["source_version"] == "1.31"
            assert call_kwargs["namespace"] == "kubernetes"

