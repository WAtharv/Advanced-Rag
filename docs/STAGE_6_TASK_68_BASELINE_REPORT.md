# Stage 6 Task 68: Redis Shared Cache Architecture & Baseline Report

**Project:** Enterprise Advanced RAG / Kubernetes SRE Copilot  
**Author:** AI Engineering Team  
**Evaluation Date:** 2026-11-11  
**Target:** Architecture Inspection, Cloud Multi-Instance Evaluation, and Redis Integration Design  

---

## 1. Executive Summary

This report documents the architectural baseline inspection and technical justification for **Stage 6 Task 68: Redis Shared Cache Integration**. 

With Tasks 64–67 complete:
1. **In-process LRU caching** with monotonic time expiration, thread-safety, and SingleFlight request coalescing is operational (Task 64).
2. **Intelligent domain caches** for embeddings, intent routing, and benchmark-gated retrieval are implemented with coordinated knowledge invalidation (Task 65).
3. **Stateless execution** across multiple application instances is enabled via PostgreSQL LangGraph checkpoint persistence and SQLAlchemy metadata persistence (Tasks 66 & 67).

This report investigates whether an optional Redis-backed shared cache is justified for multi-instance cloud deployments, specifies which cache categories are safe to share, establishes strict security rules, and defines the integration architecture.

---

## 2. Existing Caching Architecture Inspection

### 2.1 Current In-Process Cache Implementation (`src/advanced_rag/cache.py`)
The existing caching subsystem provides:
- **`LRUCache[V]`**: Generic thread-safe LRU cache using `collections.OrderedDict`, `threading.RLock`, and `time.monotonic()` for skew-immune expiration.
- **`SingleFlight[T]`**: Deduplicates concurrent identical in-flight operations within a single process.
- **`AnswerCache`**: Two-tier exact match (`_EXACT_PREFIX = "rag:exact:"`) and semantic vector similarity tier (`_SEMANTIC_KEY = "rag:semantic"`). Supports `MemoryBackend` and a basic `RedisBackend`.
- **`EmbeddingCache`**: Caches dense and sparse query vectors keyed by `dense:{model_name}:{dim}:{fingerprint}`.
- **`IntentRoutingCache`**: Caches deterministic `RouteDecision` outcomes keyed by `route:{model_name}:{prompt_version}:{fingerprint}`.
- **`RetrievalCache`**: Caches candidate chunks keyed by `{generation}:{collection}:{mode}:{fusion}:{top_k}:{filter_dict}:{fingerprint}`.
- **`invalidate_all_knowledge_caches()`**: Coordinated invalidation bumping the local generation counter and clearing the answer cache.

### 2.2 Configuration in `src/advanced_rag/config.py`
Current cache settings:
- `cache_ttl_seconds`: 3600
- `cache_default_ttl`: 3600
- `cache_max_entries`: 1000
- `semantic_cache_threshold`: 0.95
- `embedding_cache_enabled`, `embedding_cache_ttl`, `embedding_cache_max_entries`
- `intent_cache_enabled`, `intent_cache_ttl`, `intent_cache_max_entries`
- `retrieval_cache_enabled`, `retrieval_cache_ttl`, `retrieval_cache_max_entries`
- `redis_url`: optional string, currently defaults to `None`.

### 2.3 FastAPI Lifespan & Observation APIs (`src/advanced_rag/api/main.py`)
- **FastAPI Lifespan**: Initializes PostgreSQL checkpointer on startup and cleans up on shutdown. No explicit Redis connection pooling or graceful cleanup hook currently exists in the lifespan context.
- **Observability Endpoints**:
  - `GET /health`: Reports `"cache": "redis"` if `redis_url` is set, else `"in-process"`.
  - `GET /cache/stats`: Aggregates metrics from `answer_cache`, `embedding_cache`, `intent_cache`, and `retrieval_cache`.
  - `POST /cache/clear`: Invokes `invalidate_all_knowledge_caches()`.

---

## 3. Cloud Multi-Instance Deployment Justification

### 3.1 Problem Statement in Multi-Replica Deployments
In a production Kubernetes deployment:
- The `rag-api` deployment scales to multiple pods (e.g., 2–10 replicas) behind an ingress controller / Service load balancer.
- Because LangGraph checkpoints are persisted to PostgreSQL (Tasks 66 & 67), conversational turns and approval interrupts can be routed to *any* replica statelessly.
- However, **in-process caches are siloed to individual OS processes**:
  1. **Redundant Embedding Calculations**: If User A's query hits Replica 1 and User B asks the same or similar question on Replica 2, Replica 2 computes dense embeddings from scratch, wasting CPU/GPU or external API quota.
  2. **Duplicate Intent Classification**: Identical inbound queries routed across different replicas repeatedly call the LLM for intent routing.
  3. **Cold Cache Latency Spikes**: Replicas restarted during rolling updates start with 0% cache hit rates.
  4. **Stale Cache Divergence**: When a document is uploaded, updated, or deleted on Replica 1, `invalidate_all_knowledge_caches()` only increments the local in-process generation counter on Replica 1. Replicas 2, 3, etc. continue serving stale retrieval results until their local TTL expires!

### 3.2 Decision: Shared Cache is Justified as an Optional Backend
- **Verdict**: **JUSTIFIED AS OPTIONAL BACKEND (`CACHE_BACKEND=redis`)**.
- **Local Development Preservation**: For local single-node development, testing, and CI/CD without Redis, `CACHE_BACKEND=memory` remains the default.
- **Two-Tier Architecture**:
  - **L1 (In-Process LRU)**: Sub-millisecond retrieval, eliminates network round-trips for hot keys within the same process.
  - **L2 (Redis Shared Cache)**: Cross-instance sharing of expensive computations (embeddings, intent routing, retrieval results) and distributed generation-based invalidation.

---

## 4. Shared Cache Categories & Security Boundary

### 4.1 Safe to Share
| Cache Category | Key Pattern | Value Type | Cross-Instance Benefit |
| :--- | :--- | :--- | :--- |
| **Embedding Cache** | `rag:v1:embed:{model}:{dim}:{query_hash}` | JSON list of floats `[0.12, ...]` | High (saves CPU/API cost) |
| **Intent Routing** | `rag:v1:intent:{model}:{prompt_hash}:{query_hash}` | JSON `{"route": "vector", ...}` | High (eliminates LLM call) |
| **Retrieval Results** | `rag:v1:retrieval:{gen}:{collection}:{params_hash}` | JSON list of chunk dictionaries | Medium-High (saves vector DB load) |
| **Exact Answer Cache**| `rag:v1:exact:{query_hash}` | JSON `AnswerResponse` dict | High (immediate sub-10ms response) |

### 4.2 Strictly Prohibited from Sharing
To comply with security and data governance requirements:
- **SQL Execution Results**: Must NEVER be cached in Redis. Operational database query results contain dynamic live cluster/incident data.
- **SQL Approval Decisions & Proposals**: Must NEVER be stored in Redis. Approval workflows are governed exclusively by LangGraph PostgreSQL checkpoints and the auditable `approval_audit` table.
- **Authentication & RBAC**: No auth credentials or tokens in Redis.
- **User-Specific Context & PII**: Keys must be derived from normalized query fingerprints; session-specific state is excluded.

---

## 5. Architectural Requirements for Task 68

1. **Protocol & Connection Pooling**:
   - Connection pool via `redis.ConnectionPool.from_url()`.
   - Bounded timeouts (`REDIS_CONNECT_TIMEOUT=2.0s`, `REDIS_SOCKET_TIMEOUT=2.0s`).
   - TLS support (`rediss://` or SSL parameters).
2. **Safe Serialization**:
   - Strictly structured JSON (`json.dumps` / `json.loads`).
   - Zero use of `pickle` (prevents arbitrary code execution vulnerabilities).
3. **Payload Size Guard**:
   - `REDIS_MAX_PAYLOAD_BYTES` (default 1 MB). Payloads exceeding this limit are skipped with a warning.
4. **Cross-Instance Invalidation**:
   - Global generation key: `rag:v1:gen:knowledge`.
   - On document upload/delete/sync, atomic `INCR rag:v1:gen:knowledge` invalidates all cached retrieval results across all replicas in O(1).
5. **Fail-Soft Reliability**:
   - If Redis is unavailable or times out, degrade gracefully to in-process memory or compute without dropping requests.
   - Redis failure must NEVER affect PostgreSQL checkpoint persistence.
6. **Graceful Teardown**:
   - Disconnect pool during FastAPI application shutdown.

---

## 6. Baseline Verification Check

- **Installed packages**: `redis==8.1.0` is installed and verified via `pip check`.
- **Existing test suite**: All 673 repository regression tests pass cleanly.
- **Data store safety**: Embedded Qdrant (5,533 points) and SQLite `data/ops.db` (485 audit records, 5 clusters) are preserved.

