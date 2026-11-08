"""Retrieval quality evaluation runner for Stage 2.8 on sre_copilot_v1.

Evaluates 6 retrieval strategies against the 50-question curated benchmark:
1. Dense Only (BAAI/bge-small-en-v1.5, Cosine)
2. Sparse Only (BM25)
3. Hybrid (Weighted fusion, dense_weight=0.7)
4. Hybrid (Server-side RRF)
5. Hybrid + CrossEncoder Rerank (ms-marco-MiniLM-L-6-v2)
6. Metadata-Filtered (Category filter + Hybrid + Rerank)

Measures:
- Hit@1, Hit@3, Hit@5
- Precision@1, Precision@3, Precision@5
- Recall@5
- MRR
- NDCG@5
- Retrieval, Rerank, and Total latency per query
- Category-wise breakdown (Kubernetes, Helm, Prometheus, SRE)
- Error / miss analysis
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from qdrant_client import models

from advanced_rag.config import get_settings
from advanced_rag.evaluation.datasets.sre_copilot_eval_50 import EVAL_BENCHMARK_50, EvalCase50
from advanced_rag.observability import setup_logging
from advanced_rag.retrieval.retriever import Retriever
from advanced_rag.retrieval.vectorstore import HybridStore

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sre_copilot_v1"
OUTPUT_PATH = Path("eval_results/sre_copilot_retrieval_eval.json")


def _is_match(retrieved_source: str, expected_sources: list[str]) -> bool:
    ret_norm = retrieved_source.replace("\\", "/").strip()
    ret_name = Path(ret_norm).name
    for exp in expected_sources:
        exp_norm = exp.replace("\\", "/").strip()
        if ret_norm == exp_norm or ret_name == Path(exp_norm).name:
            return True
    return False


def compute_metrics_for_case(
    retrieved_sources: list[str], expected_sources: list[str]
) -> dict[str, float]:
    """Compute Hit@K, Precision@K, Recall@K, MRR, NDCG@K for a single question."""
    if not expected_sources:
        return {
            "hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0,
            "p@1": 0.0, "p@3": 0.0, "p@5": 0.0,
            "recall@5": 0.0, "mrr": 0.0, "ndcg@5": 0.0,
        }

    # Hit@K
    hit_1 = 1.0 if any(_is_match(s, expected_sources) for s in retrieved_sources[:1]) else 0.0
    hit_3 = 1.0 if any(_is_match(s, expected_sources) for s in retrieved_sources[:3]) else 0.0
    hit_5 = 1.0 if any(_is_match(s, expected_sources) for s in retrieved_sources[:5]) else 0.0

    # Precision@K: unique matched expected sources in top K / K
    def p_at_k(k: int) -> float:
        top_k = retrieved_sources[:k]
        matched = {s for s in top_k if _is_match(s, expected_sources)}
        return len(matched) / k if k > 0 else 0.0

    # Recall@5: unique matched expected sources in top 5 / len(expected_sources)
    matched_5 = {s for s in retrieved_sources[:5] if _is_match(s, expected_sources)}
    recall_5 = len(matched_5) / len(expected_sources)

    # MRR: reciprocal rank of first relevant result
    mrr = 0.0
    for rank, s in enumerate(retrieved_sources, start=1):
        if _is_match(s, expected_sources):
            mrr = 1.0 / rank
            break

    # NDCG@5 with single-credit per unique source document
    gains: list[float] = []
    credited: set[str] = set()
    for s in retrieved_sources[:5]:
        if _is_match(s, expected_sources) and s not in credited:
            credited.add(s)
            gains.append(1.0)
        else:
            gains.append(0.0)

    dcg = sum(g / math.log2(idx + 2) for idx, g in enumerate(gains))
    ideal_hits = min(len(expected_sources), 5)
    idcg = sum(1.0 / math.log2(idx + 2) for idx in range(ideal_hits))
    ndcg_5 = (dcg / idcg) if idcg > 0 else 0.0

    return {
        "hit@1": hit_1,
        "hit@3": hit_3,
        "hit@5": hit_5,
        "p@1": round(p_at_k(1), 3),
        "p@3": round(p_at_k(3), 3),
        "p@5": round(p_at_k(5), 3),
        "recall@5": round(recall_5, 3),
        "mrr": round(mrr, 3),
        "ndcg@5": round(ndcg_5, 3),
    }


def run_benchmark():
    settings = get_settings()
    setup_logging(settings.log_level)

    store = HybridStore(settings=settings, collection=COLLECTION_NAME)
    retriever = Retriever(settings=settings, store=store)

    assert store.client.collection_exists(COLLECTION_NAME), f"Collection {COLLECTION_NAME} missing!"
    point_count = store.count()
    print(f"Running evaluation against {COLLECTION_NAME} ({point_count} points)...")

    strategies: list[dict[str, Any]] = [
        {
            "id": "dense_only",
            "label": "Dense Only (bge-small-en-v1.5)",
            "kwargs": {"mode": "dense", "rerank": False, "use_hyde": False, "top_k": 20, "top_n": 5},
            "uses_filter": False,
        },
        {
            "id": "sparse_only",
            "label": "Sparse Only (BM25)",
            "kwargs": {"mode": "sparse", "rerank": False, "use_hyde": False, "top_k": 20, "top_n": 5},
            "uses_filter": False,
        },
        {
            "id": "hybrid_weighted",
            "label": "Hybrid (Weighted, α=0.7)",
            "kwargs": {"mode": "hybrid", "fusion": "weighted", "rerank": False, "use_hyde": False, "top_k": 20, "top_n": 5},
            "uses_filter": False,
        },
        {
            "id": "hybrid_rrf",
            "label": "Hybrid (RRF)",
            "kwargs": {"mode": "hybrid", "fusion": "rrf", "rerank": False, "use_hyde": False, "top_k": 20, "top_n": 5},
            "uses_filter": False,
        },
        {
            "id": "hybrid_rerank",
            "label": "Hybrid + CrossEncoder Rerank (ms-marco-MiniLM)",
            "kwargs": {"mode": "hybrid", "fusion": "weighted", "rerank": True, "use_hyde": False, "top_k": 20, "top_n": 5},
            "uses_filter": False,
        },
        {
            "id": "metadata_filtered",
            "label": "Metadata-Filtered (Category + Hybrid + Rerank)",
            "kwargs": {"mode": "hybrid", "fusion": "weighted", "rerank": True, "use_hyde": False, "top_k": 20, "top_n": 5},
            "uses_filter": True,
        },
    ]

    all_results: dict[str, Any] = {
        "collection": COLLECTION_NAME,
        "point_count": point_count,
        "benchmark_cases": len(EVAL_BENCHMARK_50),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strategies": {},
        "category_summary": {},
        "error_analysis": [],
    }

    category_results: dict[str, dict[str, list[dict[str, float]]]] = defaultdict(lambda: defaultdict(list))

    print("\n" + "=" * 90)
    print(f"{'Strategy':<45} | {'Hit@1':<6} | {'Hit@3':<6} | {'Hit@5':<6} | {'MRR':<6} | {'NDCG@5':<6} | {'Recall@5':<8} | {'Lat(ms)':<7}")
    print("=" * 90)

    for strat in strategies:
        strat_id = strat["id"]
        strat_label = strat["label"]
        kwargs = strat["kwargs"]
        uses_filter = strat["uses_filter"]

        case_metrics: list[dict[str, float]] = []
        retrieval_latencies: list[float] = []
        rerank_latencies: list[float] = []
        total_latencies: list[float] = []
        detailed_cases: list[dict[str, Any]] = []

        for case in EVAL_BENCHMARK_50:
            call_kwargs = dict(kwargs)
            if uses_filter:
                # Filter by domain keyword matching the case category in the source path
                cat_filter = models.Filter(
                    must=[models.FieldCondition(key="source", match=models.MatchText(text=case.category))]
                )
                call_kwargs["filter_"] = cat_filter

            t0 = time.perf_counter()
            # Measure raw search
            t_ret_0 = time.perf_counter()
            top_k_chunks = store.search(
                case.question,
                top_k=call_kwargs.get("top_k", 20),
                mode=call_kwargs.get("mode", "hybrid"),
                fusion=call_kwargs.get("fusion", "weighted"),
                filter_=call_kwargs.get("filter_"),
            )
            t_ret = (time.perf_counter() - t_ret_0) * 1000

            # Measure rerank
            t_rerank = 0.0
            final_chunks = top_k_chunks
            if call_kwargs.get("rerank", False) and top_k_chunks:
                t_rr_0 = time.perf_counter()
                final_chunks = retriever.rerank(case.question, top_k_chunks, top_n=call_kwargs.get("top_n", 5))
                t_rerank = (time.perf_counter() - t_rr_0) * 1000
            else:
                final_chunks = top_k_chunks[:call_kwargs.get("top_n", 5)]

            t_total = (time.perf_counter() - t0) * 1000

            retrieval_latencies.append(t_ret)
            rerank_latencies.append(t_rerank)
            total_latencies.append(t_total)

            retrieved_sources = [c.chunk.source for c in final_chunks]
            metrics = compute_metrics_for_case(retrieved_sources, case.expected_sources)
            case_metrics.append(metrics)
            category_results[strat_id][case.category].append(metrics)

            detailed_cases.append({
                "id": case.id,
                "question": case.question,
                "category": case.category,
                "expected": case.expected_sources,
                "retrieved": retrieved_sources,
                "metrics": metrics,
                "hit@5": metrics["hit@5"] > 0,
            })

            # Check for misses in the highest-performing strategy to populate error analysis
            if strat_id == "hybrid_rerank" and metrics["hit@5"] == 0:
                all_results["error_analysis"].append({
                    "id": case.id,
                    "question": case.question,
                    "category": case.category,
                    "expected": case.expected_sources,
                    "retrieved_top_5": retrieved_sources[:5],
                    "reason": "Expected document not in top 5 retrieved candidates.",
                })

        # Average metrics across all 50 cases
        avg_hit1 = sum(m["hit@1"] for m in case_metrics) / len(case_metrics)
        avg_hit3 = sum(m["hit@3"] for m in case_metrics) / len(case_metrics)
        avg_hit5 = sum(m["hit@5"] for m in case_metrics) / len(case_metrics)
        avg_p1 = sum(m["p@1"] for m in case_metrics) / len(case_metrics)
        avg_p3 = sum(m["p@3"] for m in case_metrics) / len(case_metrics)
        avg_p5 = sum(m["p@5"] for m in case_metrics) / len(case_metrics)
        avg_recall5 = sum(m["recall@5"] for m in case_metrics) / len(case_metrics)
        avg_mrr = sum(m["mrr"] for m in case_metrics) / len(case_metrics)
        avg_ndcg5 = sum(m["ndcg@5"] for m in case_metrics) / len(case_metrics)

        avg_ret_lat = sum(retrieval_latencies) / len(retrieval_latencies)
        avg_rr_lat = sum(rerank_latencies) / len(rerank_latencies)
        avg_tot_lat = sum(total_latencies) / len(total_latencies)

        all_results["strategies"][strat_id] = {
            "label": strat_label,
            "overall": {
                "hit@1": round(avg_hit1, 4),
                "hit@3": round(avg_hit3, 4),
                "hit@5": round(avg_hit5, 4),
                "precision@1": round(avg_p1, 4),
                "precision@3": round(avg_p3, 4),
                "precision@5": round(avg_p5, 4),
                "recall@5": round(avg_recall5, 4),
                "mrr": round(avg_mrr, 4),
                "ndcg@5": round(avg_ndcg5, 4),
            },
            "latency": {
                "retrieval_ms": round(avg_ret_lat, 2),
                "rerank_ms": round(avg_rr_lat, 2),
                "total_ms": round(avg_tot_lat, 2),
            },
            "detailed_cases": detailed_cases,
        }

        print(f"{strat_label:<45} | {avg_hit1:<6.3f} | {avg_hit3:<6.3f} | {avg_hit5:<6.3f} | {avg_mrr:<6.3f} | {avg_ndcg5:<6.3f} | {avg_recall5:<8.3f} | {avg_tot_lat:<7.1f}")

    print("=" * 90)

    # Category breakdown for Hybrid + CrossEncoder Rerank
    print("\nCategory Breakdown for Hybrid + CrossEncoder Rerank:")
    print(f"{'Category':<15} | {'Cases':<6} | {'Hit@1':<6} | {'Hit@3':<6} | {'Hit@5':<6} | {'MRR':<6} | {'NDCG@5':<6} | {'Recall@5':<8}")
    print("-" * 75)
    for cat in ["kubernetes", "helm", "prometheus", "sre"]:
        cat_metrics = category_results["hybrid_rerank"][cat]
        c_hit1 = sum(m["hit@1"] for m in cat_metrics) / len(cat_metrics)
        c_hit3 = sum(m["hit@3"] for m in cat_metrics) / len(cat_metrics)
        c_hit5 = sum(m["hit@5"] for m in cat_metrics) / len(cat_metrics)
        c_mrr = sum(m["mrr"] for m in cat_metrics) / len(cat_metrics)
        c_ndcg5 = sum(m["ndcg@5"] for m in cat_metrics) / len(cat_metrics)
        c_recall5 = sum(m["recall@5"] for m in cat_metrics) / len(cat_metrics)

        all_results["category_summary"][cat] = {
            "cases": len(cat_metrics),
            "hit@1": round(c_hit1, 4),
            "hit@3": round(c_hit3, 4),
            "hit@5": round(c_hit5, 4),
            "mrr": round(c_mrr, 4),
            "ndcg@5": round(c_ndcg5, 4),
            "recall@5": round(c_recall5, 4),
        }
        print(f"{cat:<15} | {len(cat_metrics):<6} | {c_hit1:<6.3f} | {c_hit3:<6.3f} | {c_hit5:<6.3f} | {c_mrr:<6.3f} | {c_ndcg5:<6.3f} | {c_recall5:<8.3f}")
    print("-" * 75)

    store.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print(f"\nEvaluation complete! Report saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    run_benchmark()

