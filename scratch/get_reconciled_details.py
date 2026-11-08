"""Reconcile arithmetic and extract complete top-5 reranked identities."""

from fastapi.testclient import TestClient
from advanced_rag.api.main import app
import json

client = TestClient(app)
query = "pod killed with exit code 137"

# 1. Raw retrieval (Rerank OFF) for Dense, Sparse, Hybrid
d_raw = client.post("/retrieve", json={"query": query, "mode": "dense", "rerank": False, "top_k": 20}).json()
s_raw = client.post("/retrieve", json={"query": query, "mode": "sparse", "rerank": False, "top_k": 20}).json()
h_raw = client.post("/retrieve", json={"query": query, "mode": "hybrid", "fusion": "rrf", "rerank": False, "top_k": 20}).json()

# 2. Reranked (Rerank ON) for Dense, Sparse, Hybrid
d_rk = client.post("/retrieve", json={"query": query, "mode": "dense", "rerank": True, "top_k": 20}).json()
s_rk = client.post("/retrieve", json={"query": query, "mode": "sparse", "rerank": True, "top_k": 20}).json()
h_rk = client.post("/retrieve", json={"query": query, "mode": "hybrid", "fusion": "rrf", "rerank": True, "top_k": 20}).json()

dense_set = set((r["source"], r["section"]) for r in d_raw["results"])
sparse_set = set((r["source"], r["section"]) for r in s_raw["results"])
hybrid_set = set((r["source"], r["section"]) for r in h_raw["results"])

shared_ds = dense_set & sparse_set
unique_dense = dense_set - sparse_set
unique_sparse = sparse_set - dense_set

print("=== RECONCILED CANDIDATE ARITHMETIC ===")
print(f"Total Dense Candidates:  {len(dense_set)}")
print(f"Total Sparse Candidates: {len(sparse_set)}")
print(f"Shared Candidates:       {len(shared_ds)} ({len(shared_ds)/20*100:.1f}%)")
print(f"Unique to Dense:         {len(unique_dense)} ({len(unique_dense)/20*100:.1f}%)")
print(f"Unique to Sparse:        {len(unique_sparse)} ({len(unique_sparse)/20*100:.1f}%)")
print(f"Total Unique Combined:   {len(dense_set | sparse_set)}")

out = {
    "dense_reranked_top5": d_rk["results"][:5],
    "sparse_reranked_top5": s_rk["results"][:5],
    "hybrid_reranked_top5": h_rk["results"][:5],
    "arithmetic": {
        "dense_count": len(dense_set),
        "sparse_count": len(sparse_set),
        "shared": len(shared_ds),
        "unique_dense": len(unique_dense),
        "unique_sparse": len(unique_sparse),
        "union": len(dense_set | sparse_set)
    }
}

with open("scratch/reranked_identities_and_arithmetic.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2)

print("\n=== TOP 5 RERANKED IDENTITIES ===")
for mode_name, rk_data in [("Dense + Rerank ON", d_rk), ("Sparse + Rerank ON", s_rk), ("Hybrid RRF + Rerank ON", h_rk)]:
    print(f"\n-- {mode_name} (Total results: {len(rk_data['results'])}) --")
    for i, r in enumerate(rk_data["results"][:5], 1):
        print(f"  #{i} [rerank: {r['rerank_score']}, retrieval: {r['retrieval_score']}, orig rank: #{r['retrieval_rank']}] {r['source']} › {r['section']}")

