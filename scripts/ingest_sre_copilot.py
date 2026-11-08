"""Ingestion runner for Stage 2.7: Ingest complete 252-doc corpus into sre_copilot_v1."""

import json
import logging
import time
from collections import Counter
from pathlib import Path

from advanced_rag.certs import enable_system_trust_store
from advanced_rag.config import get_settings
from advanced_rag.ingestion.loader import chunk_corpus, load_corpus
from advanced_rag.observability import setup_logging
from advanced_rag.retrieval.vectorstore import HybridStore

logger = logging.getLogger(__name__)

TARGET_COLLECTION = "sre_copilot_v1"
SUMMARY_PATH = Path("eval_results/ingestion_report_stage2_7.json")


def run_ingestion():
    settings = get_settings()
    setup_logging(settings.log_level)
    enable_system_trust_store()

    t_start = time.perf_counter()

    # Pre-verification of existing collections
    store_k8s = HybridStore(settings=settings, collection="k8s_ops")
    count_k8s_ops_before = store_k8s.client.count("k8s_ops").count
    count_k8s_ops_bge_small_before = store_k8s.client.count("k8s_ops_bge_small").count
    store_k8s.close()

    print(f"Pre-ingestion baseline check:")
    print(f"  k8s_ops: {count_k8s_ops_before} points")
    print(f"  k8s_ops_bge_small: {count_k8s_ops_bge_small_before} points")
    assert count_k8s_ops_before == 82, f"Expected 82 in k8s_ops, got {count_k8s_ops_before}"
    assert count_k8s_ops_bge_small_before == 82, f"Expected 82 in k8s_ops_bge_small, got {count_k8s_ops_bge_small_before}"

    # Load documents
    t_load_0 = time.perf_counter()
    documents = load_corpus()
    t_load = time.perf_counter() - t_load_0
    print(f"Loaded {len(documents)} documents in {t_load:.2f}s")
    assert len(documents) == 252, f"Expected 252 documents, got {len(documents)}"

    # Chunk corpus
    t_chunk_0 = time.perf_counter()
    chunks = chunk_corpus(documents)
    t_chunk = time.perf_counter() - t_chunk_0
    print(f"Generated {len(chunks)} chunks in {t_chunk:.2f}s")

    # Category breakdown
    cat_chunks = Counter()
    for c in chunks:
        cat = c.source.split("/")[0]
        cat_chunks[cat] += 1
    print("Chunks per category:", dict(cat_chunks))

    # Deduplicate by chunk ID
    seen_ids = set()
    unique_chunks = []
    duplicate_count = 0
    for chunk in chunks:
        if chunk.id in seen_ids:
            duplicate_count += 1
            continue
        seen_ids.add(chunk.id)
        unique_chunks.append(chunk)

    print(f"Duplicate chunks collapsed: {duplicate_count}")
    print(f"Unique chunks to index: {len(unique_chunks)}")

    sizes = [len(c.text) for c in unique_chunks]
    avg_chunk_length = sum(sizes) / len(sizes) if sizes else 0

    # Ingest into Qdrant
    target_store = HybridStore(settings=settings, collection=TARGET_COLLECTION)
    try:
        # Inspect if collection already exists
        if target_store.client.collection_exists(TARGET_COLLECTION):
            print(f"Collection {TARGET_COLLECTION} already exists! Recreating clean...")
            target_store.ensure_collection(recreate=True)
        else:
            print(f"Creating new collection {TARGET_COLLECTION}...")
            target_store.ensure_collection(recreate=False)

        # Inspect collection parameters
        coll_info = target_store.client.get_collection(TARGET_COLLECTION)
        print("Vector config:", coll_info.config.params.vectors)
        print("Sparse vector config:", coll_info.config.params.sparse_vectors)

        t_upsert_0 = time.perf_counter()
        written = target_store.upsert(unique_chunks, batch_size=64)
        t_upsert = time.perf_counter() - t_upsert_0

        actual_points = target_store.count()
        print(f"Indexed {written} chunks in {t_upsert:.2f}s")
        print(f"Actual point count in {TARGET_COLLECTION}: {actual_points}")
        assert actual_points == len(unique_chunks), f"Expected {len(unique_chunks)}, got {actual_points}"

    finally:
        target_store.close()

    t_total = time.perf_counter() - t_start

    # Post-verification of existing collections
    store_k8s_after = HybridStore(settings=settings, collection="k8s_ops")
    count_k8s_ops_after = store_k8s_after.client.count("k8s_ops").count
    count_k8s_ops_bge_small_after = store_k8s_after.client.count("k8s_ops_bge_small").count
    store_k8s_after.close()

    print(f"Post-ingestion baseline check:")
    print(f"  k8s_ops: {count_k8s_ops_after} points (was {count_k8s_ops_before})")
    print(f"  k8s_ops_bge_small: {count_k8s_ops_bge_small_after} points (was {count_k8s_ops_bge_small_before})")
    assert count_k8s_ops_after == 82
    assert count_k8s_ops_bge_small_after == 82

    # Save summary report
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "collection_name": TARGET_COLLECTION,
        "total_source_documents": len(documents),
        "documents_successfully_processed": len(documents),
        "failed_documents": 0,
        "total_generated_chunks": len(chunks),
        "unique_indexed_chunks": len(unique_chunks),
        "duplicate_chunks_count": duplicate_count,
        "point_count": actual_points,
        "chunks_per_category": dict(cat_chunks),
        "average_chunks_per_document": round(len(chunks) / len(documents), 2),
        "average_chunk_length_chars": round(avg_chunk_length, 1),
        "embedding_and_insertion_duration_seconds": round(t_upsert, 2),
        "total_ingestion_duration_seconds": round(t_total, 2),
        "dense_config": {
            "model": settings.dense_model,
            "dimensions": 384,
            "distance": "Cosine",
        },
        "sparse_config": {
            "model": "local-bm25",
            "modifier": "IDF",
        },
        "k8s_ops_preserved": count_k8s_ops_after == 82,
        "k8s_ops_bge_small_preserved": count_k8s_ops_bge_small_after == 82,
    }

    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print(f"Report saved to {SUMMARY_PATH}")
    print("INGESTION STAGE 2.7 COMPLETE AND VERIFIED!")


if __name__ == "__main__":
    run_ingestion()

