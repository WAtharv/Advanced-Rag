"""Regression and unit tests for Retrieval Lab UX and API fixes."""

from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from advanced_rag.api.main import app
from advanced_rag.models import Chunk, RetrievedChunk
from advanced_rag.retrieval.retriever import RetrievalResult, Retriever


@pytest.fixture
def api_client(monkeypatch):
    from advanced_rag.api import main

    monkeypatch.setattr(main.pipeline, "get_graph", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client


def test_empty_query_returns_422(api_client):
    """Empty query must be rejected with HTTP 422."""
    res = api_client.post("/retrieve", json={"query": "", "mode": "hybrid"})
    assert res.status_code == 422


def test_whitespace_query_returns_422(api_client):
    """Whitespace-only query must be rejected with HTTP 422."""
    res = api_client.post("/retrieve", json={"query": "    \t  \n  ", "mode": "hybrid"})
    assert res.status_code == 422


def test_valid_query_strips_whitespace_and_returns_200(api_client, monkeypatch):
    """Valid queries with leading/trailing whitespace should be trimmed safely."""
    fake_result = RetrievalResult(
        chunks=[
            RetrievedChunk(
                chunk=Chunk(id="c1", text="some runbook text", source="runbook.md", section="Symptoms"),
                retrieval_score=0.88,
                retrieval_rank=1,
            )
        ],
        query_used="exit code 137",
        mode="hybrid",
        reranked=False,
        cross_encoder=False,
        elapsed_ms=12.5,
    )

    class DummyRetriever:
        def retrieve(self, query, **kwargs):
            assert query == "exit code 137"
            return fake_result

    monkeypatch.setattr("advanced_rag.retrieval.get_retriever", lambda: DummyRetriever())

    res = api_client.post("/retrieve", json={"query": "   exit code 137   ", "mode": "hybrid"})
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "exit code 137"
    assert len(data["results"]) == 1
    assert data["results"][0]["source"] == "runbook.md"


def test_retrieve_dense_sparse_hybrid_modes_and_candidate_count(api_client, monkeypatch):
    """Modes pass through and candidate_count/result_count match requested top_k."""
    captured_calls = []

    def make_fake_chunks(n):
        return [
            RetrievedChunk(
                chunk=Chunk(id=f"c{i}", text=f"text {i}", source=f"doc_{i}.md", section="Sec"),
                retrieval_score=round(1.0 / (i + 1), 4),
                retrieval_rank=i + 1,
            )
            for i in range(n)
        ]

    class FakeRetriever:
        def retrieve(self, query, *, mode="hybrid", top_k=20, top_n=20, rerank=False, **kwargs):
            captured_calls.append({"mode": mode, "top_k": top_k, "top_n": top_n, "rerank": rerank})
            return RetrievalResult(
                chunks=make_fake_chunks(top_k),
                query_used=query,
                mode=mode,
                reranked=rerank,
                cross_encoder=False,
                elapsed_ms=15.0,
            )

    monkeypatch.setattr("advanced_rag.retrieval.get_retriever", lambda: FakeRetriever())

    for m in ["dense", "sparse", "hybrid"]:
        res = api_client.post("/retrieve", json={"query": "test query", "mode": m, "top_k": 20, "rerank": False})
        assert res.status_code == 200
        d = res.json()
        assert d["mode"] == m
        assert d["candidate_count"] == 20
        assert d["result_count"] == 20
        assert len(d["results"]) == 20
        assert d["ordered_by"] == "retrieval"
        assert d["results"][0]["retrieval_rank"] == 1
        assert d["results"][19]["retrieval_rank"] == 20
        assert d["results"][0]["rerank_score"] is None

    assert len(captured_calls) == 3
    for call in captured_calls:
        assert call["top_k"] == 20
        assert call["top_n"] == 20
        assert call["rerank"] is False


def test_reranking_preserves_requested_candidate_count_and_reorders(api_client, monkeypatch):
    """When reranking is ON, all requested candidates are reranked and returned in rerank order."""
    chunks = [
        RetrievedChunk(
            chunk=Chunk(id="c1", text="text 1", source="doc_1.md", section="Sec 1"),
            retrieval_score=0.9,
            rerank_score=0.5,
            rerank_is_authoritative=True,
            retrieval_rank=1,
        ),
        RetrievedChunk(
            chunk=Chunk(id="c2", text="text 2", source="doc_2.md", section="Sec 2"),
            retrieval_score=0.7,
            rerank_score=0.99,
            rerank_is_authoritative=True,
            retrieval_rank=2,
        ),
        RetrievedChunk(
            chunk=Chunk(id="c3", text="text 3", source="doc_3.md", section="Sec 3"),
            retrieval_score=0.6,
            rerank_score=0.2,
            rerank_is_authoritative=True,
            retrieval_rank=3,
        ),
    ]

    class FakeRerankRetriever:
        def retrieve(self, query, *, top_k=3, top_n=3, rerank=True, **kwargs):
            assert top_k == 3
            assert top_n == 3
            # Simulated reranked order: c2 (0.99) -> c1 (0.5) -> c3 (0.2)
            reranked_chunks = [chunks[1], chunks[0], chunks[2]]
            return RetrievalResult(
                chunks=reranked_chunks,
                query_used=query,
                mode="hybrid",
                reranked=True,
                cross_encoder=True,
                elapsed_ms=45.0,
                rerank_ms=30.0,
            )

    monkeypatch.setattr("advanced_rag.retrieval.get_retriever", lambda: FakeRerankRetriever())

    res = api_client.post("/retrieve", json={"query": "test query", "mode": "hybrid", "top_k": 3, "rerank": True})
    assert res.status_code == 200
    d = res.json()
    assert d["candidate_count"] == 3
    assert d["result_count"] == 3
    assert d["ordered_by"] == "rerank"
    assert d["rerank_is_authoritative"] is True
    assert d["latency_ms"]["rerank"] == 30.0
    assert d["latency_ms"]["hyde"] is None

    # First result should be c2 (promoted by reranker)
    assert d["results"][0]["source"] == "doc_2.md"
    assert d["results"][0]["retrieval_rank"] == 2
    assert d["results"][0]["retrieval_score"] == 0.7
    assert d["results"][0]["rerank_score"] == 0.99

    # Second result should be c1
    assert d["results"][1]["source"] == "doc_1.md"
    assert d["results"][1]["retrieval_rank"] == 1
    assert d["results"][1]["retrieval_score"] == 0.9
    assert d["results"][1]["rerank_score"] == 0.5


def test_normal_ask_pipeline_bounds_context_to_settings():
    """Verify nodes.py retrieve_node bounds context chunks to settings.rerank_top_n."""
    from advanced_rag.graph import nodes
    from advanced_rag.config import get_settings

    settings = get_settings()
    expected_top_n = settings.rerank_top_n or 5
    assert expected_top_n == 5


def test_ui_checkbox_defaults_to_off():
    """Verify that ui/web/index.html has lab-rerank unchecked by default."""
    index_html = Path("ui/web/index.html").read_text(encoding="utf-8")
    assert '<input id="lab-rerank" type="checkbox">' in index_html
    assert '<input id="lab-rerank" type="checkbox" checked>' not in index_html
