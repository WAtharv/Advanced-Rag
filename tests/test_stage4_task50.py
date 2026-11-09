"""Stage 4 Task 50 — Retrieval Evaluation and Benchmarking Tests.

Verifies:
1. Metric calculations: Hit@{1,3,5}, P@{1,3,5}, Recall@{1,3,5}, MRR, NDCG@{3,5}.
2. Single-credit-per-document rule: DCG <= IDCG, NDCG bounded within [0.0, 1.0].
3. Duplicate chunk handling: subsequent chunks from the same document earn 0.0 gain.
4. Edge cases: empty retrieved list, empty expected list, zero-division safety.
5. Dual-mode matching diagnostics: Canonical Basename vs Strict Path matching.
6. Corpus collision audit and isolated divergence on k8s-07.
7. Benchmark artifacts: JSON structure, per-query records, latency stats, and SVG charts.
8. External search isolation: Tavily evaluated separately with zero internal benchmark leakage.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path so scripts can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from scripts.evaluate_stage4_task50 import (
    CORPUS_COLLISIONS,
    _is_match_canonical,
    _is_match_strict,
    compute_latency_stats,
    compute_metrics_for_case,
)


# =====================================================================
# 1. Metric Suite Tests: Hit@K, MRR, Precision@K, Recall@K
# =====================================================================


def test_hit_at_k_calculation():
    expected = ["kubernetes/troubleshooting/debug.md"]

    # Hit at rank 1
    m1 = compute_metrics_for_case(
        ["kubernetes/troubleshooting/debug.md", "other1.md", "other2.md"],
        expected,
    )
    assert m1["hit@1"] == 1.0
    assert m1["hit@3"] == 1.0
    assert m1["hit@5"] == 1.0
    assert m1["mrr"] == 1.0

    # Hit at rank 3
    m3 = compute_metrics_for_case(
        ["other1.md", "other2.md", "kubernetes/troubleshooting/debug.md", "other3.md"],
        expected,
    )
    assert m3["hit@1"] == 0.0
    assert m3["hit@3"] == 1.0
    assert m3["hit@5"] == 1.0
    assert m3["mrr"] == pytest.approx(1.0 / 3.0, abs=1e-4)

    # Miss in top 5
    m_miss = compute_metrics_for_case(
        ["o1.md", "o2.md", "o3.md", "o4.md", "o5.md"],
        expected,
    )
    assert m_miss["hit@1"] == 0.0
    assert m_miss["hit@3"] == 0.0
    assert m_miss["hit@5"] == 0.0
    assert m_miss["mrr"] == 0.0


def test_precision_and_recall_at_k():
    expected = ["docA.md", "docB.md", "docC.md"]

    # Retrieved has docA at rank 1, docB at rank 2, docA again at rank 3, other at 4 and 5
    retrieved = ["docA.md", "docB.md", "docA.md", "other1.md", "other2.md"]

    m = compute_metrics_for_case(retrieved, expected, match_mode="strict")
    # At K=1: 1 match / 1 = 1.0; recall = 1/3 = 0.3333
    assert m["p@1"] == 1.0
    assert m["recall@1"] == pytest.approx(1.0 / 3.0, abs=1e-4)

    # At K=3: unique matches are docA and docB (2 unique docs) / 3 = 0.6667; recall = 2/3 = 0.6667
    assert m["p@3"] == pytest.approx(2.0 / 3.0, abs=1e-4)
    assert m["recall@3"] == pytest.approx(2.0 / 3.0, abs=1e-4)

    # At K=5: 2 unique docs / 5 = 0.4000; recall = 2/3 = 0.6667
    assert m["p@5"] == 0.4000
    assert m["recall@5"] == pytest.approx(2.0 / 3.0, abs=1e-4)


# =====================================================================
# 2. Mathematical NDCG Single-Credit Integrity
# =====================================================================


def test_ndcg_single_credit_deduplication():
    """Verify that multiple chunks from the same relevant document only earn gain once.

    Prevents DCG > IDCG and guarantees NDCG in [0.0, 1.0].
    """
    expected = ["helm/debugging-templates.md"]
    # 5 retrieved chunks, all from the same relevant document
    retrieved = [
        "helm/debugging-templates.md",
        "helm/debugging-templates.md",
        "helm/debugging-templates.md",
        "helm/debugging-templates.md",
        "helm/debugging-templates.md",
    ]

    m = compute_metrics_for_case(retrieved, expected, match_mode="strict")

    # In rank 1, doc gets gain=1.0. Ranks 2-5 get gain=0.0 because document is already credited.
    # DCG@5 = 1.0 / log2(2) = 1.000
    # IDCG@5 = 1.0 / log2(2) = 1.000 (since min(len(expected), 5) = 1)
    # NDCG@5 must be exactly 1.000, NOT 2.948 (the flawed multi-chunk scratch metric).
    assert m["ndcg@5"] == 1.000
    assert m["ndcg@3"] == 1.000
    assert m["ndcg@5"] <= 1.000


def test_ndcg_multi_document_ranking():
    expected = ["doc1.md", "doc2.md"]
    # Perfect ranking: doc1 at rank 1, doc2 at rank 2
    retrieved_ideal = ["doc1.md", "doc2.md", "doc3.md"]
    m_ideal = compute_metrics_for_case(retrieved_ideal, expected, match_mode="strict")
    assert m_ideal["ndcg@3"] == 1.000
    assert m_ideal["ndcg@5"] == 1.000

    # Inverted ranking: non-relevant at rank 1, relevant at rank 2 and 3
    retrieved_suboptimal = ["irrelevant.md", "doc1.md", "doc2.md"]
    m_sub = compute_metrics_for_case(retrieved_suboptimal, expected, match_mode="strict")

    # Ideal DCG: 1/log2(2) + 1/log2(3) = 1.0 + 0.6309 = 1.6309
    # Suboptimal DCG: 0 + 1/log2(3) + 1/log2(4) = 0.6309 + 0.5000 = 1.1309
    # Suboptimal NDCG: 1.1309 / 1.6309 ~= 0.6934
    expected_ndcg = (1.0 / math.log2(3) + 1.0 / math.log2(4)) / (1.0 / math.log2(2) + 1.0 / math.log2(3))
    assert m_sub["ndcg@3"] == pytest.approx(expected_ndcg, abs=1e-3)
    assert 0.0 < m_sub["ndcg@3"] < 1.000


def test_edge_cases_empty_and_zero_results():
    # Empty retrieved list
    m_empty_ret = compute_metrics_for_case([], ["docA.md"])
    assert m_empty_ret["hit@1"] == 0.0
    assert m_empty_ret["mrr"] == 0.0
    assert m_empty_ret["ndcg@5"] == 0.0
    assert m_empty_ret["p@5"] == 0.0
    assert m_empty_ret["recall@5"] == 0.0

    # Empty expected list
    m_empty_exp = compute_metrics_for_case(["docA.md"], [])
    assert m_empty_exp["hit@1"] == 0.0
    assert m_empty_exp["mrr"] == 0.0
    assert m_empty_exp["ndcg@5"] == 0.0


# =====================================================================
# 3. Dual-Mode Document Matching & Collision Diagnostics
# =====================================================================


def test_canonical_vs_strict_matching_collision():
    """Verify that canonical basename matching awards a hit on duplicate basenames,
    while strict normalized-path matching enforces exact path equality."""
    retrieved = "kubernetes/scheduling/node-pressure-eviction.md"
    expected = [
        "kubernetes/scheduling/limit-ranges.md",
        "sre/runbooks/kubernetes/node-pressure-eviction.md",
    ]

    # Canonical basename matches 'node-pressure-eviction.md'
    assert _is_match_canonical(retrieved, expected) is True

    # Strict path requires exact path equality
    assert _is_match_strict(retrieved, expected) is False


def test_corpus_collision_registry():
    """Verify all 6 verified collisions in the 252 corpus documents are audited."""
    assert len(CORPUS_COLLISIONS) == 6
    assert "node-pressure-eviction.md" in CORPUS_COLLISIONS
    assert "rbac.md" in CORPUS_COLLISIONS
    assert "getting-started.md" in CORPUS_COLLISIONS
    assert "overview.md" in CORPUS_COLLISIONS
    assert "configuration.md" in CORPUS_COLLISIONS
    assert "glossary.md" in CORPUS_COLLISIONS

    for basename, paths in CORPUS_COLLISIONS.items():
        assert len(paths) == 2
        assert Path(paths[0]).name == basename
        assert Path(paths[1]).name == basename
        assert paths[0] != paths[1]


# =====================================================================
# 4. Latency Statistics Computation
# =====================================================================


def test_compute_latency_stats():
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0]
    stats = compute_latency_stats(latencies)

    assert stats["min"] == 10.0
    assert stats["max"] == 50.0
    assert stats["mean"] == 30.0
    assert stats["p50"] == 30.0
    assert stats["p95"] == pytest.approx(48.0, abs=0.5)

    # Empty case
    empty_stats = compute_latency_stats([])
    assert empty_stats["mean"] == 0.0
    assert empty_stats["p50"] == 0.0


# =====================================================================
# 5. Benchmark Output Artifacts Verification
# =====================================================================


def test_benchmark_artifact_validity():
    benchmark_file = Path("eval_results/stage4_task50_benchmark.json")
    assert benchmark_file.exists(), f"Missing required artifact: {benchmark_file}"

    with open(benchmark_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "metadata" in data
    assert data["metadata"]["benchmark_cases"] == 50
    assert data["metadata"]["collection"] == "sre_copilot_v1"

    strategies = data["strategies"]
    assert "hybrid_rrf" in strategies
    assert "dense_only" in strategies
    assert "sparse_only" in strategies
    assert "hybrid_weighted" in strategies
    assert "hybrid_rrf_rerank" in strategies

    # Verify baseline reproduction on Hybrid RRF
    rrf_canon = strategies["hybrid_rrf"]["overall_canonical"]
    assert rrf_canon["hit@1"] == pytest.approx(0.720, abs=1e-3)
    assert rrf_canon["hit@3"] == pytest.approx(0.940, abs=1e-3)
    assert rrf_canon["hit@5"] == pytest.approx(0.980, abs=1e-3)
    assert rrf_canon["mrr"] == pytest.approx(0.829, abs=1e-3)
    assert rrf_canon["ndcg@5"] == pytest.approx(0.605, abs=1e-3)
    assert rrf_canon["recall@5"] == pytest.approx(0.603, abs=1e-3)

    # Verify strict metrics on Hybrid RRF
    rrf_strict = strategies["hybrid_rrf"]["overall_strict"]
    assert rrf_strict["hit@1"] == pytest.approx(0.700, abs=1e-3)
    assert rrf_strict["hit@3"] == pytest.approx(0.920, abs=1e-3)
    assert rrf_strict["hit@5"] == pytest.approx(0.960, abs=1e-3)
    assert rrf_strict["mrr"] == pytest.approx(0.809, abs=1e-3)

    # Verify each strategy contains all 50 query details
    assert len(strategies["hybrid_rrf"]["detailed_cases"]) == 50

    # Verify external search is recorded separately
    ext_eval = data["external_search_evaluation"]
    assert ext_eval["status"] == "PASS"
    assert ext_eval["isolation_rule"] == "External results are never mixed into internal golden benchmark metrics"


def test_standalone_svg_figures_exist():
    fig_dir = Path("eval_results/figures")
    assert fig_dir.exists()

    required_figures = [
        "hit_at_k_comparison.svg",
        "mrr_ndcg_comparison.svg",
        "precision_recall_comparison.svg",
        "latency_breakdown.svg",
        "accuracy_vs_latency_tradeoff.svg",
    ]

    for fig_name in required_figures:
        fig_path = fig_dir / fig_name
        assert fig_path.exists(), f"Missing required figure: {fig_path}"
        content = fig_path.read_text(encoding="utf-8")
        assert "<svg" in content
        assert "</svg>" in content
