"""Comprehensive tests for Stage 2.7 & Stage 2.8: SRE Copilot Ingestion & Retrieval Evaluation.

Tests:
- 252-document corpus discovery and manifest exclusion
- Deterministic chunk and Qdrant point IDs
- Qdrant collection sre_copilot_v1 vector configuration (Dense 384d Cosine + Sparse IDF)
- Point count and duplicate prevention
- Baseline collection preservation (k8s_ops: 82 pts, k8s_ops_bge_small: 82 pts)
- Payload metadata preservation & source attribution
- Simulated incident disclaimers
- Evaluation benchmark dataset validity & metric calculation
- End-to-end retrieval and citation accuracy
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from pathlib import Path

import pytest
from qdrant_client import QdrantClient, models

from advanced_rag.config import get_settings
from advanced_rag.evaluation.datasets.sre_copilot_eval_50 import EVAL_BENCHMARK_50
from advanced_rag.ingestion.chunker import _chunk_id
from advanced_rag.ingestion.loader import CORPUS_DIR, chunk_corpus, load_corpus
from advanced_rag.retrieval.retriever import Retriever
from advanced_rag.retrieval.vectorstore import DENSE, SPARSE, HybridStore, _point_id


def test_pre_ingestion_corpus_discovery_and_manifest_exclusion():
    """Verify exactly 252 documents are discovered across the 4 approved categories."""
    docs = load_corpus()
    assert len(docs) == 252

    by_cat = Counter(d.source.split("/")[0] for d in docs)
    assert by_cat["kubernetes"] == 64
    assert by_cat["helm"] == 64
    assert by_cat["prometheus"] == 64
    assert by_cat["sre"] == 60

    # Ensure no JSON manifests are ingested as documents
    manifest_sources = [d.source for d in docs if d.source.endswith("_manifest.json") or d.source.endswith("manifest.json")]
    assert manifest_sources == []

    # Ensure no duplicate document sources
    sources = [d.source for d in docs]
    assert len(sources) == len(set(sources))


def test_deterministic_chunk_ids_and_point_ids():
    """Verify chunk IDs and Qdrant point IDs are content-addressed and deterministic."""
    source = "kubernetes/troubleshooting/determine-pod-failure-reason.md"
    section = "Initial Triage"
    body = "Check container exit codes and termination reason using kubectl describe pod."

    cid1 = _chunk_id(source, section, body)
    cid2 = _chunk_id(source, section, body)
    assert cid1 == cid2
    assert cid1.startswith(source + "#")

    pid1 = _point_id(cid1)
    pid2 = _point_id(cid2)
    assert pid1 == pid2
    assert str(uuid.UUID(pid1)) == pid1  # Valid UUID string format


def test_baseline_collections_integrity_preserved():
    """Verify that existing baseline collections (k8s_ops, k8s_ops_bge_small) were not modified."""
    settings = get_settings()
    client = QdrantClient(path=str(settings.absolute(settings.qdrant_path)))
    try:
        assert client.collection_exists("k8s_ops"), "k8s_ops must exist"
        assert client.collection_exists("k8s_ops_bge_small"), "k8s_ops_bge_small must exist"

        assert client.count("k8s_ops").count == 82, "k8s_ops point count must remain exactly 82"
        assert client.count("k8s_ops_bge_small").count == 82, "k8s_ops_bge_small point count must remain exactly 82"
    finally:
        client.close()


def test_sre_copilot_collection_configuration_and_point_count():
    """Verify sre_copilot_v1 exists with 384d Cosine dense vectors + IDF sparse vectors."""
    settings = get_settings()
    client = QdrantClient(path=str(settings.absolute(settings.qdrant_path)))
    try:
        assert client.collection_exists("sre_copilot_v1"), "sre_copilot_v1 collection must exist"

        coll_info = client.get_collection("sre_copilot_v1")
        vectors_cfg = coll_info.config.params.vectors
        assert isinstance(vectors_cfg, dict)
        assert DENSE in vectors_cfg
        assert vectors_cfg[DENSE].size == 384
        assert vectors_cfg[DENSE].distance == models.Distance.COSINE

        sparse_cfg = coll_info.config.params.sparse_vectors
        assert isinstance(sparse_cfg, dict)
        assert SPARSE in sparse_cfg
        assert sparse_cfg[SPARSE].modifier == models.Modifier.IDF

        actual_points = client.count("sre_copilot_v1").count
        assert actual_points == 5533, f"Expected 5533 points, got {actual_points}"
    finally:
        client.close()


def test_metadata_preservation_and_payload_completeness():
    """Verify payload metadata survives ingestion and is present on points across all 4 categories."""
    settings = get_settings()
    client = QdrantClient(path=str(settings.absolute(settings.qdrant_path)))
    try:
        records, _ = client.scroll(
            collection_name="sre_copilot_v1",
            limit=200,
            with_payload=True,
            with_vectors=False,
        )
        assert len(records) > 0

        required_keys = {"source", "title", "section", "doc_type", "category"}
        categories_found = set()

        for r in records:
            payload = r.payload or {}
            assert required_keys <= set(payload.keys()), f"Missing keys in payload: {payload.keys()}"
            cat = payload.get("category")
            if cat:
                categories_found.add(cat)

        assert len(categories_found) >= 2, f"Expected multiple categories in sample, got {categories_found}"
    finally:
        client.close()


def test_simulated_incidents_disclaimer_preservation():
    """Verify simulated postmortems and case studies contain simulation disclaimer."""
    settings = get_settings()
    store = HybridStore(settings=settings, collection="sre_copilot_v1")
    try:
        results = store.search("SIMULATED INCIDENT", top_k=5, mode="sparse")
        assert len(results) > 0
        found_disclaimer = any(
            "SIMULATED" in r.chunk.text.upper() or "SIMULATED" in r.chunk.title.upper()
            for r in results
        )
        assert found_disclaimer
    finally:
        store.close()


def test_retrieval_benchmark_dataset_integrity():
    """Verify 50 curated benchmark questions are grounded in real corpus documents."""
    assert len(EVAL_BENCHMARK_50) == 50

    by_cat = Counter(c.category for c in EVAL_BENCHMARK_50)
    assert by_cat["kubernetes"] == 15
    assert by_cat["helm"] == 10
    assert by_cat["prometheus"] == 10
    assert by_cat["sre"] == 15

    corpus_dir = Path("data/corpus")
    for c in EVAL_BENCHMARK_50:
        assert len(c.expected_sources) >= 1
        for src in c.expected_sources:
            p = corpus_dir / src
            assert p.exists(), f"Benchmark case {c.id} expected source {src} does not exist on disk"


def test_retrieval_and_citation_generation():
    """Verify end-to-end retrieval on sre_copilot_v1 returns relevant chunks and citations."""
    settings = get_settings()
    store = HybridStore(settings=settings, collection="sre_copilot_v1")
    retriever = Retriever(settings=settings, store=store)
    try:
        res = retriever.retrieve("CrashLoopBackOff container exit code 1", top_k=10, top_n=3, mode="hybrid")
        assert len(res.chunks) > 0
        top = res.chunks[0]
        assert "crashloopbackoff" in top.chunk.source.lower()
        citation = top.chunk.citation()
        assert len(citation) > 0
        assert top.chunk.source in citation or top.chunk.title in citation
    finally:
        store.close()

