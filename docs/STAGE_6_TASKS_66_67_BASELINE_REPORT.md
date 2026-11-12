# Stage 6 Tasks 66 & 67: Architecture Baseline & Inspection Report

**Project:** Enterprise Advanced RAG / Kubernetes SRE Copilot
**Author:** AI Engineering Team
**Evaluation Date:** 2026-11-11
**Target:** PostgreSQL Checkpointing, Metadata Persistence & Concurrency Recovery

---

## 1. Executive Summary

This report establishes the baseline architectural state of the Enterprise Advanced RAG / Kubernetes SRE Copilot prior to implementing Stage 6 Tasks 66 (PostgreSQL Checkpointing & Metadata Persistence) and 67 (Recovery, Restart Safety & Concurrency).

### Critical Finding: Persistence Separation
There is a fundamental architectural distinction between **LangGraph Checkpoint State** and **Application Operational Records**:
- **LangGraph Checkpoints**: Store conversational execution graph snapshots, pending thread state, task interrupts, and channel state. Currently, the default checkpointer is in-memory (`MemorySaver`), meaning all active threads and pending human approval interrupts vanish upon process restart.
- **Application Metadata & Audit**: Stored via SQLAlchemy declarative models in SQLite (`data/ops.db`) or PostgreSQL. In Stage 5 Task 63, the `approval_audit` table was migrated to persistent database storage with Alembic migrations.

---

## 2. Current Architecture Inspection (12 Checkpoints)

### 2.1 LangGraph Graph Construction & Compilation
- **Location:** `src/advanced_rag/graph/builder.py`, `src/advanced_rag/graph/pipeline.py`
- **Execution Mode:** **Synchronous execution**.
  - Pipeline entrypoints invoke the compiled graph synchronously via `graph.invoke(...)` and query thread state with `graph.get_state(...)`.
  - Re-entry / resumption occurs via `graph.invoke(Command(resume=decision_payload), config=config)`.
- **Finding:** The checkpointer implementation must strictly be synchronous (`PostgresSaver` via `psycopg` and `psycopg_pool`), not asynchronous (`AsyncPostgresSaver`), to match the pipeline's synchronous execution model.

### 2.2 Existing Checkpointer Implementation & Lifecycle
- **Location:** `src/advanced_rag/graph/builder.py` (`_make_checkpointer`, `get_graph`)
- **Current Behavior:**
  ```python
  def _make_checkpointer(settings: Settings):
      if settings.checkpoint_backend != "postgres":
          return MemorySaver()
      if not settings.postgres_dsn:
          return MemorySaver()
      ...
      try:
          saver = PostgresSaver.from_conn_string(conn).__enter__()
          saver.setup()
          return saver
      except Exception:
          return MemorySaver()
  ```
- **Identified Gaps:**
  1. **Unbounded Connection Lifecycle:** Calling `PostgresSaver.from_conn_string(conn).__enter__()` binds a single raw connection for the process lifetime instead of a managed connection pool (`psycopg_pool.ConnectionPool`).
  2. **Silent Fallback:** When `CHECKPOINT_BACKEND=postgres` is explicitly requested, catching `Exception` and falling back silently to `MemorySaver` masks configuration errors and creates false confidence of persistence.
  3. **No Graceful Shutdown:** No hook to close database pool connections during application shutdown.

### 2.3 Current Checkpointer Configuration
- **Location:** `src/advanced_rag/config.py`
- **Current Settings:**
  - `checkpoint_backend: Literal["memory", "postgres"] = "memory"`
  - `postgres_dsn: str | None = None`
  - `postgres_readonly_dsn: str | None = None`
- **Identified Gaps:** Lacks dedicated pooling knobs (`POSTGRES_POOL_MIN_SIZE`, `POSTGRES_POOL_MAX_SIZE`, `POSTGRES_POOL_TIMEOUT`), retry backoff knobs, and dedicated checkpoint DSN separation.

### 2.4 FastAPI Lifespan Startup & Shutdown
- **Location:** `src/advanced_rag/api/main.py` (`lifespan`)
- **Current State:**
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      settings = get_settings()
      setup_logging(settings.log_level)
      enable_system_trust_store()
      pipeline.get_graph()
      yield
      logger.info("Shutting down API")
  ```
- **Identified Gaps:**
  - Lifespan pre-compiles the graph via `pipeline.get_graph()`, but does not manage a managed PostgreSQL pool context or close database connection pools on shutdown.

### 2.5 Existing SQLAlchemy Database Engines & Session Factories
- **Location:** `src/advanced_rag/db/session.py`
- **Current State:**
  - `get_engine()` creates a singleton `Engine` with `pool_pre_ping=True`.
  - `get_db_session()` provides a transactional context manager with auto-commit and auto-rollback on error.
  - `init_db()` invokes `Base.metadata.create_all(bind=eng)`.
- **Portability:** Configured with `check_same_thread=False` when URL is SQLite, and fully supports PostgreSQL via `postgresql+psycopg://`.

### 2.6 Existing SQLite and PostgreSQL Connection Configuration
- **SQLite:** Defaults to `data/ops.db` for local operations.
- **PostgreSQL:** Driven by `POSTGRES_DSN` and `POSTGRES_READONLY_DSN`.
  - In Task 59, strict read-only enforcement was implemented: `POSTGRES_READONLY_DSN` is required when PostgreSQL is configured for Text2SQL.

### 2.7 Alembic Migrations and Database Initialization
- **Location:** `alembic/env.py`, `alembic/versions/`
- **Revisions:**
  - `001_baseline_registry.py`: Creates `document_metadata` and `ingestion_runs`.
  - `002_approval_audit_table.py`: Creates `approval_audit`.
- **Compatibility:** Uses `render_as_batch=True` for portable SQLite and PostgreSQL execution.
- **Safety Boundary:** Alembic manages application tables (`document_metadata`, `ingestion_runs`, `approval_audit`). It must NOT manage or conflict with `langgraph-checkpoint-postgres` internal tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`).

### 2.8 Application Metadata Persistence
- **Models:** Defined in `src/advanced_rag/db/models.py`.
- **`document_metadata`**: Preserves document provenance, hash, chunk count, and status.
- **`ingestion_runs`**: Tracks ingestion duration, successes, failures.
- **`approval_audit`**: 290 persistent audit records currently stored in `data/ops.db`.

### 2.9 Stage 5 SQL Approval Interrupts & Resume Workflow
- **Location:** `src/advanced_rag/graph/nodes.py` (`sql_approval_node`), `src/advanced_rag/graph/pipeline.py` (`resume`, `pending_approval`)
- **Interrupt Mechanism:** Uses LangGraph `interrupt(...)` to pause execution before SQL execution.
- **Binding:** Proposal ID, SQL hash, and thread ID are bound to prevent tampering or replay attacks.
- **Current Limitation:** Under `MemorySaver`, restarting the process loses the thread snapshot, causing `KeyError: no run found for thread_id ...` on `/approve`.

### 2.10 Stage 6 Tasks 64/65 Cache Integration
- In-process caches (`LRUCache`, `EmbeddingCache`, `IntentRoutingCache`, `RetrievalCache`) operate in-memory with monotonic TTL and LRU ceilings.
- **Preservation Principle:** Cache contents are transient process-local optimizations; they do not require PostgreSQL persistence. Invalidation hooks must remain operational.

### 2.11 Existing API Conversation / Thread Handling
- Endpoint `/ask` accepts optional `thread_id` (or generates a random UUID).
- Endpoint `/approve` and `/pending/{thread_id}` require `thread_id`.
- Under multi-worker deployment or process restart, sticky routing or distributed checkpointing is required.

### 2.12 Current Dependency Versions & Installed PostgreSQL Drivers
- `langgraph`: `1.2.19`
- `langgraph-checkpoint`: `4.3.1`
- `langgraph-checkpoint-postgres`: `3.2.0`
- `psycopg`: `3.3.6` (with `psycopg-binary` and `psycopg-pool 3.3.3`)
- `SQLAlchemy`: `2.1.6`
- `alembic`: `1.20.1`
- Verification: `python -m pip check` confirms **no broken requirements**. All required PostgreSQL driver packages are already properly installed and declared in `requirements.txt` and `pyproject.toml`.

---

## 3. Persistent vs Transient State Matrix

| State Component | Current Storage | Survives Process Restart? | Target Storage in Task 66/67 |
| :--- | :--- | :--- | :--- |
| **LangGraph Checkpoints** | `MemorySaver` (in-memory) | **NO (Lost on restart)** | `PostgresSaver` (`checkpoints`, `checkpoint_blobs`) |
| **Pending Approval Interrupts** | `MemorySaver` (in-memory) | **NO (Lost on restart)** | `PostgresSaver` |
| **Document Metadata Registry** | SQLite `document_metadata` | **YES** | SQLite / PostgreSQL (Alembic) |
| **Ingestion Run History** | SQLite `ingestion_runs` | **YES** | SQLite / PostgreSQL (Alembic) |
| **SQL Approval Audit Logs** | SQLite `approval_audit` | **YES** | SQLite / PostgreSQL (Alembic) |
| **Vector Store Corpus** | Embedded Qdrant (disk) | **YES** | Embedded Qdrant |
| **Query Embedding Cache** | `EmbeddingCache` (in-memory) | **NO (Transient cache)** | Preserved in-memory |
| **Intent-Routing Cache** | `IntentRoutingCache` (in-memory) | **NO (Transient cache)** | Preserved in-memory |
| **Retrieval Result Cache** | `RetrievalCache` (in-memory) | **NO (Transient cache)** | Preserved in-memory |

---

## 4. Key Implementation Strategy for Tasks 66 & 67

1. **PostgreSQL Checkpointer Pool Manager**:
   - Create a dedicated connection pool manager using `psycopg_pool.ConnectionPool`.
   - Provide configurable pool size, connection timeout, and SSL mode.
   - Fail closed without silent in-memory fallback when `CHECKPOINT_BACKEND=postgres`.
2. **Lifespan Integration**:
   - Initialize connection pool and run `saver.setup()` once at startup.
   - Cleanly close pool during application shutdown.
3. **Recovery Verification**:
   - Ensure thread recovery after restart succeeds with exact state preservation.
   - Verify approval resume workflow maintains proposal ID and SQL hash binding.
4. **Local SQLite Preservation**:
   - Ensure SQLite development workflows remain fully functional when `POSTGRES_DSN` is empty.
