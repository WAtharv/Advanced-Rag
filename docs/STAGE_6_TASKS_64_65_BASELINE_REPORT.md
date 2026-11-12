# Stage 6 — Tasks 64 & 65: Baseline Caching Architecture Report
## Enterprise Advanced RAG / Kubernetes SRE Copilot

**Date**: 2026-11-11
**Auditor/Author**: Antigravity Core Systems Architecture Engine
**Project**: Enterprise Advanced RAG / Kubernetes SRE Copilot
**Deliverable**: `docs/STAGE_6_TASKS_64_65_BASELINE_REPORT.md`
**Scope**: In-depth architectural inspection of the caching subsystem prior to Stage 6 modifications.

---

## 1. Executive Summary

This report establishes the baseline technical audit of the caching subsystem in the Enterprise Advanced RAG / Kubernetes SRE Copilot before implementing Tasks 64 (In-Process Cache Improvements) and 65 (Intelligent Caching).

The current caching implementation consists solely of a two-tier question-to-answer cache (`src/advanced_rag/cache.py`) supporting exact and semantic matching. Crucial intermediate operations—specifically dense/sparse embedding generation, intent routing classification, and retrieval search executions—are **completely uncached**. Furthermore, the existing in-process cache backend lacks capacity ceilings for exact matching, uses non-monotonic wall-clock time for expiration, provides no thread synchronization, lacks single-flight request coalescing, and has zero invalidation hooks tied to document lifecycle events.

---

## 2. Inventory of Current Caching Operations

### 2.1 Currently Cached Operations
| Operation | Implementation Class / Function | Storage Backend | Key Structure |
| :--- | :--- | :--- | :--- |
| **Exact Answer Cache** | `AnswerCache.lookup()` / `.store()` | `MemoryBackend._values` (or Redis) | `rag:exact:<sha256[:32]>` of lowercase whitespace-normalized question |
| **Semantic Answer Cache** | `AnswerCache._semantic_lookup()` | `MemoryBackend._lists["rag:semantic"]` | Linear scan of max 500 JSON payloads computing cosine similarity |
| **Settings Resolution** | `advanced_rag.config.get_settings` | `@functools.lru_cache` | In-memory function cache |
| **Embedding Model Loader** | `get_embedder`, `get_reranker` | `@functools.lru_cache` | Process-wide singleton model instance |
| **Graph Compilation** | `advanced_rag.graph.builder.get_graph` | `@functools.lru_cache` | Compiled StateGraph instance |
| **Text2SQL Generator** | `advanced_rag.text2sql.generator.get_generator`| `@functools.lru_cache` | Singleton generator instance |
| **Concurrency Semaphore** | `text2sql.executor._get_semaphore` | `@functools.lru_cache` | Semaphore keyed by concurrency limit |

### 2.2 Currently Uncached Operations
1. **Embedding Generation (`Embedder.embed_query`, `Embedder.embed_documents`)**:
   - Every single query submitted to `retriever.retrieve()` generates a fresh 384-dimensional dense ONNX vector and sparse BM25 vector from scratch.
   - HyDE hypothetical documents generate fresh embeddings every time.
   - Semantic answer cache lookup computes a fresh query embedding on every question, even when the exact cache has already missed.
2. **Intent Routing Decisions (`route_node`)**:
   - In live LLM mode, every query reaching `route_node` executes an LLM JSON completion (`get_llm().complete_json(..., RouteDecision, system=prompts.ROUTER_SYSTEM)`).
   - Identical queries (e.g. `"List all clusters"`, `"How to troubleshoot OOMKilled"`) invoke the full LLM router every single time.
3. **Retrieval Queries (`HybridStore.search`)**:
   - Every retrieval call executes queries against embedded Qdrant (dense query points and sparse BM25 points) followed by client-side or server-side RRF fusion.
   - No result caching exists, even for repeated identical queries within short time windows.
4. **Guardrail Validations (`guardrail_input`, `guardrail_output`)**:
   - Outbound LLM safety and inbound scope evaluations re-run for identical prompts.

---

## 3. Systematic Answers to Mandatory Audit Questions

### 1. Which operations are currently cached?
Only full end-to-end question-to-answer payloads via `AnswerCache`, plus singleton loaders cached via Python's `@lru_cache`.

### 2. Which operations are not cached?
Embedding vector generation, LLM intent routing decisions, Qdrant hybrid retrieval chunk results, and guardrail validations.

### 3. Which caches are enabled or disabled by default?
- `enable_cache: bool = True` in `Settings`: Answer cache is enabled by default.
- Redis caching is disabled by default because `REDIS_URL` is unset, causing the system to fall back to `MemoryBackend`.
- Semantic caching is enabled by default if `enable_cache=True` and dense embedding models are functional. It latches to disabled if embedder loading throws an exception.

### 4. How cache keys are generated?
- **Exact Cache Key**:
  ```python
  def _fingerprint(question: str) -> str:
      return hashlib.sha256(" ".join(question.lower().split()).encode()).hexdigest()[:32]
  ```
  Prefixed with `"rag:exact:"`.
- **Semantic Cache**:
  Stored in an unindexed list (`"rag:semantic"`). The query is embedded on every lookup, and cosine similarity is computed across all non-expired entries in the list.

### 5. Whether cache entries expire?
- **Mechanism**: Expiration timestamp is recorded as `time.time() + ttl` (using wall-clock time).
- **Flaws Identified**:
  - `time.time()` is subject to NTP clock skew, leap seconds, and system time adjustments. Monotonic time (`time.monotonic()`) is not used.
  - In `MemoryBackend`, expired exact entries are deleted lazily **only during a `get()` call** on that specific key. If a key is never queried again, it leaks memory indefinitely.
  - The semantic entries list (`_lists["rag:semantic"]`) filters out expired entries during iterations but **never actually prunes or deletes them** from the list; it only trims the tail when exceeding `max_len`.

### 6. Whether caches have maximum capacity?
- **Exact Cache**: **NO CAPACITY LIMIT**. `MemoryBackend._values` is a bare Python dictionary (`dict[str, tuple[float, str]]`). Under sustained distinct queries, memory grows unbounded.
- **Semantic Cache**: Has `max_semantic_entries: int = 500`. However, eviction is purely FIFO (`del bucket[: len(bucket) - max_len]`), rather than Least Recently Used (LRU).

### 7. How cache invalidation currently works?
- Invalidation is solely all-or-nothing manual clearing:
  - `AnswerCache.clear()` flushes all keys via `POST /cache/clear`.
  - There is no granular, key-level, prefix-level, or collection-level invalidation.

### 8. Whether concurrent requests can cause duplicate computation?
- **Race Conditions**: `MemoryBackend` has **zero thread locks** (`threading.Lock` or `threading.RLock`). Concurrent threads reading/writing `self._values` and `self._lists` can suffer race conditions.
- **Duplicate Computation**: Multiple concurrent requests with identical queries will all suffer cache misses simultaneously, invoke the LLM and embedding pipelines in parallel, and duplicate computation without request coalescing.

### 9. Whether cached values can become stale after document ingestion or deletion?
- **Staleness Confirmed**:
  - `POST /documents/upload` indexes new documents into Qdrant but does not touch `AnswerCache`.
  - `DELETE /documents/{id}` deletes chunks from Qdrant but does not touch `AnswerCache`.
  - `sync_corpus()` updates documents but does not touch `AnswerCache`.
  - Consequently, queries matching cached answers will continue serving outdated knowledge for up to `cache_ttl_seconds` (default 3,600 seconds = 1 hour).

---

## 4. Current Configuration Parameters (`config.py`)

The existing caching configuration in `Settings` consists of only 3 parameters:

```python
# ---------- Cache ----------
redis_url: str | None = None
cache_ttl_seconds: int = 3600
semantic_cache_threshold: float = 0.95
enable_cache: bool = True
```

No settings currently exist for:
- Maximum memory entries or capacity bounds.
- Embedding cache TTL or capacity.
- Intent routing cache TTL or capacity.
- Retrieval result caching toggle, TTL, or capacity.

---

## 5. Architectural Gap Analysis & Remediation Plan

| Feature Area | Baseline Defect | Stage 6 Target Architecture |
| :--- | :--- | :--- |
| **Clock Source** | Wall-clock `time.time()` prone to clock skew | Monotonic clock (`time.monotonic()`) for all TTL tracking |
| **Capacity & Eviction** | Unbounded exact cache dict; FIFO semantic list | Strict LRU eviction via `collections.OrderedDict` with configurable `max_entries` |
| **Thread Safety** | Zero locking; race conditions under concurrency | Reentrant mutex (`threading.RLock`) on all mutations |
| **Request Coalescing** | Concurrent identical requests duplicate LLM/embedding | Single-flight execution barrier (`SingleFlight`) for identical in-flight keys |
| **Granular Operations** | Only `get`, `set`, `clear` | `get`, `set`, `delete`, `clear`, `invalidate`, `cleanup_expired` |
| **Intermediate Caches** | Only final answer cached | Dedicated, isolated LRU caches for: <br>1. Query Embeddings<br>2. Intent Routing Decisions<br>3. Benchmark-gated Retrieval |
| **Invalidation Lifecycle** | Zero integration with ingestion/deletion | Automatic generation-based or targeted cache invalidation on upload/delete/sync |
| **Observability** | Only log messages | Metrics tracking: hits, misses, evictions, expirations, hit rates |

---

## 6. Baseline Verification Sign-off

The baseline has been thoroughly audited. No source code has been altered yet. All findings documented above are verified against the active repository state.

**Baseline Verdict**: Ready for Stage 6 implementation.
