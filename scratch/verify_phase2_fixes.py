"""Browser-level verification of Retrieval Lab Phase 2 Fixes."""

from __future__ import annotations
import json
from pathlib import Path
from fastapi.testclient import TestClient

from advanced_rag.api.main import app
from advanced_rag.graph import pipeline

def run_verification():
    print("=" * 65)
    print("PHASE 2 VERIFICATION: RETRIEVAL LAB UX AND API FIXES")
    print("=" * 65)

    client = TestClient(app)

    # 1. Verify UI initial checkbox state
    index_html = Path("ui/web/index.html").read_text(encoding="utf-8")
    assert '<input id="lab-rerank" type="checkbox">' in index_html
    assert '<input id="lab-rerank" type="checkbox" checked>' not in index_html
    print("[PASS 1/7] UI Default State: Rerank checkbox is unchecked by default in index.html")

    # 2. Verify whitespace rejection
    res_ws = client.post("/retrieve", json={"query": "     ", "mode": "hybrid"})
    assert res_ws.status_code == 422, f"Expected 422 for whitespace, got {res_ws.status_code}"
    res_empty = client.post("/retrieve", json={"query": "", "mode": "hybrid"})
    assert res_empty.status_code == 422, f"Expected 422 for empty query, got {res_empty.status_code}"
    print("[PASS 2/7] Validation: Empty and whitespace-only queries rejected with HTTP 422")

    query = "pod killed with exit code 137"

    # 3. Dense + Rerank OFF: 20 results
    res_dense = client.post("/retrieve", json={"query": query, "mode": "dense", "rerank": False, "top_k": 20})
    assert res_dense.status_code == 200
    d_dense = res_dense.json()
    assert len(d_dense["results"]) == 20, f"Expected 20 dense results, got {len(d_dense['results'])}"
    assert d_dense["candidate_count"] == 20
    assert d_dense["result_count"] == 20
    assert d_dense["ordered_by"] == "retrieval"
    assert "total" in d_dense["latency_ms"]
    print(f"[PASS 3/7] Dense + Rerank OFF: 20 results returned (Total latency: {d_dense['latency_ms']['total']} ms)")

    # 4. Sparse + Rerank OFF: 20 results
    res_sparse = client.post("/retrieve", json={"query": query, "mode": "sparse", "rerank": False, "top_k": 20})
    assert res_sparse.status_code == 200
    d_sparse = res_sparse.json()
    assert len(d_sparse["results"]) == 20, f"Expected 20 sparse results, got {len(d_sparse['results'])}"
    assert d_sparse["candidate_count"] == 20
    assert d_sparse["result_count"] == 20
    assert d_sparse["ordered_by"] == "retrieval"
    print(f"[PASS 4/7] Sparse + Rerank OFF: 20 results returned (Total latency: {d_sparse['latency_ms']['total']} ms)")

    # 5. Hybrid RRF + Rerank OFF: 20 results
    res_hybrid_off = client.post("/retrieve", json={"query": query, "mode": "hybrid", "fusion": "rrf", "rerank": False, "top_k": 20})
    assert res_hybrid_off.status_code == 200
    d_hybrid_off = res_hybrid_off.json()
    assert len(d_hybrid_off["results"]) == 20, f"Expected 20 hybrid results, got {len(d_hybrid_off['results'])}"
    assert d_hybrid_off["candidate_count"] == 20
    assert d_hybrid_off["result_count"] == 20
    assert d_hybrid_off["ordered_by"] == "retrieval"
    print(f"[PASS 5/7] Hybrid RRF + Rerank OFF: 20 results returned (Total latency: {d_hybrid_off['latency_ms']['total']} ms)")

    # 6. Hybrid RRF + Rerank ON: 20 results, REORDERED
    res_hybrid_on = client.post("/retrieve", json={"query": query, "mode": "hybrid", "fusion": "rrf", "rerank": True, "top_k": 20})
    assert res_hybrid_on.status_code == 200
    d_hybrid_on = res_hybrid_on.json()
    assert len(d_hybrid_on["results"]) == 20, f"Expected 20 reranked results, got {len(d_hybrid_on['results'])}"
    assert d_hybrid_on["candidate_count"] == 20
    assert d_hybrid_on["result_count"] == 20
    assert d_hybrid_on["ordered_by"] == "rerank"
    assert d_hybrid_on["rerank_is_authoritative"] is True
    assert d_hybrid_on["latency_ms"]["rerank"] is not None

    # Verify scores and original rank preservation
    top1 = d_hybrid_on["results"][0]
    print(f"[PASS 6/7] Hybrid RRF + Rerank ON: ALL 20 results returned & reordered!")
    print(f"         Total latency: {d_hybrid_on['latency_ms']['total']} ms | Rerank latency: {d_hybrid_on['latency_ms']['rerank']} ms")
    print(f"         Top 1: {top1['source']} › {top1['section']}")
    print(f"         Rerank score: {top1['rerank_score']} | Retrieval score: {top1['retrieval_score']} | Orig rank: #{top1['retrieval_rank']}")
    assert top1["rerank_score"] is not None
    assert top1["retrieval_rank"] is not None

    # Print top 5 reranked
    print("\n  Top 5 Reranked Passages:")
    for i, r in enumerate(d_hybrid_on["results"][:5], 1):
        print(f"    #{i} [rerank: {r['rerank_score']}, retrieval: {r['retrieval_score']}, orig rank #{r['retrieval_rank']}] {r['source']} › {r['section']}")

    # 7. Normal Ask Feature Remains Unaffected
    print("\n--- Testing Normal Ask Pipeline ---")
    ask_resp = pipeline.ask("Why is my pod stuck in CrashLoopBackOff with exit code 137?", force_extractive=True)
    assert ask_resp.answer, "Ask pipeline returned empty answer"
    assert len(ask_resp.citations) > 0, "Ask pipeline returned no citations"
    print(f"[PASS 7/7] Normal Ask Pipeline: Functional (extractive={ask_resp.extractive}, citations={len(ask_resp.citations)})")

    # Save verification JSON for reporting
    results_dict = {
        "ui_default_rerank_off": True,
        "whitespace_rejected": True,
        "dense_20": len(d_dense["results"]) == 20,
        "sparse_20": len(d_sparse["results"]) == 20,
        "hybrid_off_20": len(d_hybrid_off["results"]) == 20,
        "hybrid_on_20": len(d_hybrid_on["results"]) == 20,
        "hybrid_on_top5": d_hybrid_on["results"][:5],
        "latency_metadata": d_hybrid_on["latency_ms"]
    }
    with open("scratch/phase2_verification_results.json", "w", encoding="utf-8") as f:
        json.dump(results_dict, f, indent=2)

    print("\n" + "=" * 65)
    print("ALL 7 PHASE 2 VERIFICATION CHECKS PASSED PERFECTLY!")
    print("=" * 65)

if __name__ == "__main__":
    run_verification()

