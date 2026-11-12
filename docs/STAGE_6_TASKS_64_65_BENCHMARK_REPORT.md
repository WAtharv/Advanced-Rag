# Stage 6 Tasks 64 & 65: Advanced Caching & Intelligent Cache Optimization Benchmark Report

**Project:** Enterprise Advanced RAG / Kubernetes SRE Copilot
**Author:** AI Engineering Team
**Evaluation Date:** 2026-11-11
**Environment:** Python 3.11.9 (Windows x86_64), ONNX Runtime (CPU), Embedded Qdrant
**Dataset:** Curated 50-question SRE Copilot benchmark (`EVAL_BENCHMARK_50`) across Kubernetes, Helm, Prometheus, and SRE operational topics

---

## Executive Summary

Stage 6 implements in-process cache optimizations (Task 64) and intelligent multi-tier caching (Task 65) without introducing Redis or breaking single-process storage constraints.

### Key Highlights
1. **Embedding Cache (Task 65.1)**:
   - Eliminates redundant ONNX dense vector computations.
   - Reduces query embedding latency from **16.87ms** cold to **0.006ms** warm (**2,765x speedup**).
   - SingleFlight request coalescing ensures simultaneous identical in-flight embedding requests execute exactly once.
2. **Intent Routing Cache (Task 65.2)**:
   - Caches deterministic LLM routing decisions namespaced by routing model and prompt hash.
   - Reduces intent classification latency from **212.17ms** cold to **0.008ms** warm (**27,201x speedup**).
   - Context-dependent state and dynamic permissions are strictly excluded from the cache.
3. **Retrieval Result Cache (Task 65.3 - Benchmark Gated)**:
   - Cold retrieval mean latency: **124.98ms** (P50: 109.04ms, P95: 136.27ms).
   - Warm retrieval mean latency: **0.138ms** (P50: 0.134ms, P95: 0.166ms) (**904.6x speedup**).
   - **Quality Preservation**: Retrieval metrics are **100% identical** between cold and warm retrieval across all 50 golden questions:
     - `Hit@1`: 0.7200 (Cold) vs 0.7200 (Warm)
     - `Hit@5`: 0.9800 (Cold) vs 0.9800 (Warm)
     - `MRR`: 0.8290 (Cold) vs 0.8290 (Warm)
     - `NDCG@5`: 0.9623 (Cold) vs 0.9623 (Warm)
   - **Gating Recommendation**: `KEEP_DISABLED_DEFAULT` (`retrieval_cache_enabled: bool = False`). While quality and fidelity are perfectly preserved, AnswerCache already intercepts identical user queries at the API edge (0.04ms), and EmbeddingCache accelerates hybrid search. Keeping retrieval caching opt-in avoids memory overhead on rapidly changing corpora.
4. **Answer Cache (Task 64)**:
   - Exact match hit: **0.0437ms** mean.
   - Semantic match hit: **0.3726ms** mean.
   - Uncached miss: **0.3161ms** mean.

---

## 1. Benchmarking Methodology

The repeatable benchmark script `scripts/benchmark_stage6_cache.py` measures real execution times using high-resolution monotonic timers (`time.perf_counter()`).

### 1.1 Evaluated Dimensions
- **Latency Percentiles**: Mean, P50 (median), and P95 latency across cold misses and warm hits.
- **Hit Rates on Workload Streams**: 100-request streams with varying repetition levels (0%, 25%, 50%, 75%).
- **Retrieval Metric Parity**: Strict comparison of `Hit@1`, `Hit@3`, `Hit@5`, `MRR`, and `NDCG@5` on the 50-query curated golden dataset.
- **Fidelity Verification**: Byte-level and chunk-ID comparison between fresh Qdrant hybrid retrieval outputs and cached outputs.

---

## 2. Benchmark Results

### 2.1 Embedding Cache (Task 65.1)

Model: `BAAI/bge-small-en-v1.5` (384-dimensional dense vectors on CPU).

| Metric | Cold (Miss) | Warm (Hit) | Improvement |
| :--- | :--- | :--- | :--- |
| **Mean Latency** | 16.869 ms | 0.006 ms | **2,765.5x faster** |
| **P50 Latency** | 6.447 ms | 0.005 ms | **1,289.4x faster** |
| **P95 Latency** | 7.388 ms | 0.007 ms | **1,055.4x faster** |

#### Workload Repetition Scaling (100 Requests)

| Workload Scenario | Observed Hit Rate | Mean Latency | P95 Latency | Total Hits / Misses |
| :--- | :--- | :--- | :--- | :--- |
| **0% Repetition** (New queries drawn from pool) | 66.7% | 0.012 ms | 0.033 ms | 80 hits / 40 misses |
| **25% Repetition** (25% repeats of seen queries) | 68.1% | 0.009 ms | 0.026 ms | 81 hits / 38 misses |
| **50% Repetition** (50% repeats of seen queries) | 68.1% | 0.009 ms | 0.027 ms | 81 hits / 38 misses |
| **75% Repetition** (75% repeats of seen queries) | 68.1% | 0.006 ms | 0.017 ms | 81 hits / 38 misses |

*Note: In repetitive query streams, sub-query embedding latency drops from ~17ms to <0.01ms.*

---

### 2.2 Intent Routing Cache (Task 65.2)

Router configuration: Fast LLM router (`llama-fast` / Groq 8B) with router prompt hash namespacing.

| Metric | Cold (LLM Call) | Warm (Cache Hit) | Improvement |
| :--- | :--- | :--- | :--- |
| **Mean Latency** | 212.169 ms | 0.008 ms | **27,201.2x faster** |
| **P50 Latency** | 212.927 ms | 0.005 ms | **42,585.4x faster** |
| **P95 Latency** | 265.804 ms | 0.024 ms | **11,075.2x faster** |

*Benefit: In high-traffic deployments, eliminating the LLM router round-trip saves ~212ms per request and conserves LLM token quota.*

---

### 2.3 Retrieval Cache & Quality Gating (Task 65.3)

Evaluated against the complete 50-query golden benchmark on collection `sre_copilot_v1` using hybrid retrieval (dense ONNX + local BM25).

#### Retrieval Latency

| Metric | Cold (Vector Search) | Warm (RetrievalCache) | Improvement |
| :--- | :--- | :--- | :--- |
| **Mean Latency** | 124.980 ms | 0.138 ms | **904.6x faster** |
| **P50 Latency** | 109.036 ms | 0.134 ms | **813.7x faster** |
| **P95 Latency** | 136.271 ms | 0.166 ms | **820.9x faster** |

#### Retrieval Quality Verification (Cold vs Warm)

| Metric | Cold Hybrid Retrieval | Warm Cached Retrieval | Difference |
| :--- | :--- | :--- | :--- |
| **Hit@1** | 0.7200 (72.0%) | 0.7200 (72.0%) | **0.0000 (Exact match)** |
| **Hit@3** | 0.9400 (94.0%) | 0.9400 (94.0%) | **0.0000 (Exact match)** |
| **Hit@5** | 0.9800 (98.0%) | 0.9800 (98.0%) | **0.0000 (Exact match)** |
| **MRR** | 0.8290 | 0.8290 | **0.0000 (Exact match)** |
| **NDCG@5** | 0.9623 | 0.9623 | **0.0000 (Exact match)** |
| **Fidelity Check** | Passed (100% chunk ID & score identity) | Passed (100% chunk ID & score identity) | **Zero drift** |

---

### 2.4 Answer Cache (Task 64)

AnswerCache operating on memory backend with monotonic timestamps and LRU eviction:

| Mode | Mean Latency | P50 Latency | P95 Latency | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Exact Match** | 0.0437 ms | 0.0402 ms | 0.0664 ms | Normalised key lookup in LRUCache |
| **Semantic Match** | 0.3726 ms | 0.3541 ms | 0.5410 ms | Embedding query + cosine threshold evaluation |
| **Cache Miss** | 0.3161 ms | 0.3082 ms | 0.3112 ms | Embedding query + miss against stored candidates |

---

## 3. Retrieval Result Cache Gating Decision

### Decision: `KEEP_DISABLED_DEFAULT` (`RETRIEVAL_CACHE_ENABLED=false`)

#### Rationale:
1. **Sufficient Acceleration via Edge and Embedding Tiers**:
   - `AnswerCache` already intercepts identical and near-identical questions at the earliest possible stage in `cache_node`, delivering end-to-end responses in `<0.05ms`.
   - `EmbeddingCache` eliminates ~17ms of query encoding overhead for retrieval operations.
2. **Memory Footprint**:
   - `RetrievedChunk` payloads contain full chunk texts and metadata. Storing 500 cached retrieval result sets consumes significant in-process memory.
3. **Invalidation Complexity on Dynamic Corpora**:
   - In production environments where documentation updates, deletions, and sync jobs occur periodically, maintaining fine-grained vectorstore result caches requires aggressive invalidation.
4. **Production Configuration**:
   - The architecture fully supports retrieval caching. Deployments with high-concurrency read-heavy search traffic on static documentation can safely enable it with `RETRIEVAL_CACHE_ENABLED=true` in `.env`.

---

## 4. Invalidation Flow Verification

The invalidation mechanism ensures zero stale knowledge leaks:
1. **Document Upload/Replace (`/documents`, `replace_document`)**:
   - Automatically invokes `invalidate_all_knowledge_caches()`.
   - Clears `AnswerCache` (exact and semantic).
   - Atomically bumps generation counter in `RetrievalCache` (O(1) invalidation).
   - Leaves `EmbeddingCache` and `IntentRoutingCache` intact.
2. **Document Delete (`delete_document`)**:
   - Triggers `invalidate_all_knowledge_caches()`.
3. **Corpus Sync (`CorpusSynchronizer.execute_sync`)**:
   - When new, updated, or deleted files are committed to Qdrant, triggers `invalidate_all_knowledge_caches()`.
4. **Manual API Clear (`POST /cache/clear`)**:
   - Flushes all caches including answer, embedding, intent, and retrieval.
