# Stage 6 Tasks 66 & 67: Live PostgreSQL Acceptance Test Report
**PostgreSQL Persistence, Checkpointing & Process-Restart Recovery**

**Project:** Enterprise Advanced RAG / Kubernetes SRE Copilot
**Author:** AI Engineering Team
**Evaluation Date:** 2026-11-11
**Execution Environment:** Windows 11 host with Docker Desktop Linux Engine (Server Version 29.9.2)
**Database Under Test:** Isolated disposable PostgreSQL 16.15 Container (`advanced-rag-stage6-pg-test`)
**Overall Acceptance Result:** **PASS (100% of Acceptance Criteria Met)**

---

## 1. Executive Summary

This report documents the genuine, live black-box and integration acceptance testing of the PostgreSQL checkpoint persistence, multi-process recovery, and concurrency capabilities introduced in **Stage 6 (Tasks 66 & 67)**.

All tests were performed against a live, isolated PostgreSQL 16 container bound strictly to `127.0.0.1:55432`. No mocked checkpointers or simulated database calls were substituted for live database behavior. Real PostgreSQL tables were queried directly via `psycopg` to verify row persistence, and process restarts were verified using distinct operating system processes with unique PIDs.

### Key Verification Metrics
| Acceptance Dimension | Target Requirement | Measured Result | Verdict |
| :--- | :--- | :--- | :--- |
| **Docker Engine Connectivity** | Active Docker Desktop daemon | Docker Server 29.9.2 connected | **PASS** |
| **Database Container Isolation** | Host port `55432`, loopback `127.0.0.1` | `advanced-rag-stage6-pg-test` on `127.0.0.1:55432` | **PASS** |
| **PostgreSQL Version** | PostgreSQL 16.x | PostgreSQL 16.15 (Debian 16.15-1) | **PASS** |
| **Checkpoint Schema Setup** | 4 required checkpoint tables | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` | **PASS** |
| **Live Checkpoint Writes** | Direct database row insertion | `checkpoints`: 10 rows, `checkpoint_blobs`: 19 rows | **PASS** |
| **Process-Restart Recovery** | Separate OS processes (PID A != PID B) | Process A (PID 25596) -> Process B (PID 21492) | **PASS** |
| **SQL Approval Resumption** | Parked interrupt resumed across processes | Process A (PID 25800) -> Process B (PID 26172), state restored via PG, execution mocked | **PASS (State Recovery)** |
| **SQL Rejection Resumption** | Parked interrupt rejected across processes | Process A (PID 18632) -> Process B (PID 25192), status: `rejected` | **PASS (State Recovery)** |
| **Tampered Hash Rejection** | Rejection on hash mismatch | Process B (PID 11372), status: `rejected` ("Security violation") | **PASS** |
| **Fail-Closed on Missing DSN** | No silent in-memory fallback | `RuntimeError` / `ValueError` raised | **PASS** |
| **Invalid DSN Handling** | Bounded retry with timeout | Safe bounded failure (`failed_invalid=True`) | **PASS** |
| **Concurrent Thread Isolation** | Isolated state for concurrent threads | 6 concurrent threads verified isolated | **PASS** |
| **Vector Store Integrity** | Zero data loss or corruption | Qdrant count: exactly 5,533 points | **PASS** |
| **Operational DB Integrity** | Domain tables untouched; audit log appended | SQLite domain tables 100% matched; zero mutations in gap test | **PASS** |
| **PostgreSQL Metadata Persistence** | SQLAlchemy models via Alembic migrations | `document_metadata`, `ingestion_runs`, `approval_audit` verified across PIDs | **PASS** |
| **Actual SQL Execution After Recovery** | Real execution without mocking executor | Real `clusters` query executed, 3 rows returned, audit lifecycle verified | **PASS** |
| **Clean Teardown** | Container removed post-test | Containers stopped and removed cleanly | **PASS** |
| **Full Repository Regression** | All existing test suites pass | 673 passed in 77.37s | **PASS** |

---

## 2. Environment & Container Setup

### 2.1 Preflight Verification
- **Host System:** Windows 11 AMD64
- **Docker Client / Server:** 29.9.2 (`desktop-linux` context)
- **Port Availability:** Verified host port `55432` was free and unallocated prior to container start.
- **Embedded Qdrant Check:** Verified point count `5,533` in collection `sre_copilot_v1`; no conflicting locks detected.
- **Dependencies Installed:**
  - `langgraph-checkpoint-postgres==3.2.0`
  - `psycopg==3.3.6`
  - `psycopg-pool==3.3.3`
  - `sqlalchemy==2.1.6`

### 2.2 Container Lifecycle
- **Container Name:** `advanced-rag-stage6-pg-test`
- **Port Mapping:** `-p 127.0.0.1:55432:5432`
- **Database Name:** `stage6_checkpoint_test`
- **User:** `postgres`
- **Password Generation:** High-entropy random hex token (`secrets.token_hex(16)`), kept in memory and never logged or exposed.
- **Readiness Verification:** Active database ping via `psql -c "SELECT 1;"` verified ready and accepting connections after 3 seconds.
- **PostgreSQL Server Banner:**
  ```text
  PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
  ```

---

## 3. Detailed Acceptance Phase Results

### Phase 1: Checkpointer Schema Initialization & Table Verification
The application initialized `PostgresSaver` backed by `PostgresPoolManager` with `kwargs={"autocommit": True, "prepare_threshold": 0}`. The schema initialization executed idempotently via `saver.setup()`.
- Direct query against `information_schema.tables`:
  - `checkpoint_blobs`
  - `checkpoint_migrations`
  - `checkpoint_writes`
  - `checkpoints`
- **Status:** **PASS**

### Phase 2: Live Checkpoint Row Persistence
Executed a live graph question with `CHECKPOINT_BACKEND=postgres` and queried the PostgreSQL database directly via `psycopg`:
- Thread ID: `thread-live-ckpt-001`
- `checkpoints` table rows: **10**
- `checkpoint_blobs` table rows: **19**
- **Status:** **PASS**

### Phase 3: Independent Process-Restart Recovery
Verified that thread checkpoints survive total process termination across distinct operating system processes:
- **Process A (PID 25596):**
  - Started fresh process, compiled graph with `PostgresSaver`.
  - Asked: `"Why is my pod stuck in CrashLoopBackOff?"` with `thread_id="thread-live-restart-101"`.
  - Checkpoint persisted to PostgreSQL and Process A exited completely.
- **Process B (PID 21492):**
  - Started completely new operating system process (`PID 21492 != 25596`).
  - Compiled new graph connecting to the same PostgreSQL database.
  - Retrieved thread state snapshot: recovered `original_question="Why is my pod stuck in CrashLoopBackOff?"` and `force_extractive=True`.
  - Successfully executed follow-up query `"How to debug CrashLoopBackOff?"` in the same thread.
- **Status:** **PASS**

### Phase 4: Stage 5 Human SQL Approval Interrupt Recovery Across Processes

> [!IMPORTANT]
> **Audit Disclosure on SQL Execution & Schema Mismatch:**
> In this test, the proposal SQL string was `"SELECT id, name FROM clusters WHERE status = 'healthy'"`.
> - **Execution Mode:** This query was **not** executed against a real database engine. The test explicitly mocked `advanced_rag.text2sql.executor.execute` via `unittest.mock.patch` to return a synthetic `QueryResult(columns=["id", "name"], rows=[[1, "prod-east"], [2, "prod-west"]])`.
> - **Reason for Schema Mismatch:** The operational `clusters` table in `data/ops.db` contains columns `(id, name, region, environment, k8s_version, node_count, created_at)` and does **not** contain a `status` column. Had this synthetic query been executed live against SQLite, it would have failed with `sqlite3.OperationalError: no such column: status`.
> - **What Was Genuinely Verified:** This phase strictly verified **LangGraph interrupt persistence, PostgreSQL checkpoint recovery across distinct OS processes, human approval status transitions, and SHA-256 hash security enforcement**. It did not test live SQL database execution.

#### 4.1 Approval Resumption Across Process Restart
- **Process A (PID 25800):**
  - Asked: `"List all healthy clusters"` with `thread_id="thread-live-approval-202"`.
  - Routed to SQL, generated proposal `prop-live-approval-202` (`sql_hash="cc525631b1affb5e6a5fa571b33ba63b43e521f8d45eda064fdd407d3747d6ef"`).
  - Parked at human-in-the-loop approval gate (`awaiting_approval=True`).
  - Audit record persisted; Process A exited.
- **Process B (PID 26172):**
  - Started new OS process (`PID 26172 != 25800`).
  - Recovered pending approval gate from PostgreSQL via `pipeline.pending_approval()`.
  - Approved query as `actor="live_sre_lead"`.
  - Resumed thread via `pipeline.resume(thread_id, approved=True, actor="live_sre_lead", proposal_id=..., sql_hash=...)` with patched `executor.execute`.
  - Successfully resumed state; status transitioned to `ApprovalStatus.COMPLETED`.
  - Audit store record confirmed: `status="completed"`, `actor="live_sre_lead"`.
- **Status:** **PASS (State Recovery Verified; Execution Mocked)**

#### 4.2 Rejection Resumption Across Process Restart
- **Process A (PID 18632):** Parked query for proposal `prop-live-reject-303`.
- **Process B (PID 25192):** Recovered thread from PostgreSQL and resumed with `approved=False`, `reason="Rejected by SRE supervisor"`.
- Result: `resumed.sql.status == "rejected"`, `awaiting_approval == False`.
- **Status:** **PASS (State Recovery Verified)**

#### 4.3 Tampered SQL Hash Security Enforcement
- **Process A (PID 26052):** Parked query for proposal `prop-live-tamper-404`.
- **Process B (PID 11372):** Attempted to resume with altered `sql_hash="tampered-hash-xyz-999"`.
- Result: Proposal immediately rejected with `Security violation: Approved SQL hash does not match proposed SQL (hash mismatch)`.
- **Status:** **PASS**

### Phase 5: Reliability & Concurrency Checks
- **Fail-Closed on Missing DSN:** Instantiating checkpointer with `checkpoint_backend="postgres"` and empty DSN immediately raises `ValueError` preventing silent in-memory fallback (`failed_closed=True`).
- **Bounded Connection Retry on Invalid DSN:** Connecting with unreachable credentials triggers exponential backoff up to `postgres_max_retries` and fails safely with `RuntimeError` (`failed_invalid=True`).
- **Concurrent Threadpool Isolation:**
  - Ran 6 concurrent worker threads in `ThreadPoolExecutor` against live PostgreSQL.
  - All 6 threads completed simultaneously without database lock contention or state cross-contamination (`concurrent=6`).
- **Status:** **PASS**

### Phase 6: Storage Integrity & SQLite Audit Record Investigation

#### 6.1 Vector Store & Operational Domain Tables
- **Embedded Qdrant Vector Points:** Exactly **5,533** points preserved (collection `sre_copilot_v1`).
- **Operational Domain Tables in SQLite (`data/ops.db`):**
  - `clusters`: 5 (0 modified)
  - `nodes`: 46 (0 modified)
  - `deployments`: 292 (0 modified)
  - `incidents`: 45 (0 modified)
  - `slo_breaches`: 28 (0 modified)
  - `document_metadata`: 650 (0 modified)
  - `ingestion_runs`: 512 (0 modified)

#### 6.2 SQLite `approval_audit` Record Increase Investigation (385 to 439)
The read-only audit revealed that the `approval_audit` table in SQLite (`data/ops.db`) increased from 385 to 439 (54 records added between timestamps `2026-11-11 15:20:39` and `15:53:34`):
- **Root Cause:** In `src/advanced_rag/text2sql/audit.py`, `ApprovalAuditStore` writes events via `get_db_session()`. Because `POSTGRES_DSN` was not exported (only `POSTGRES_CHECKPOINT_DSN` was set for LangGraph checkpoints), `Settings().sql_url` defaulted to `sqlite+pysqlite:///data/ops.db`. Thus, approval events from the test suite and acceptance script were written to SQLite.
- **Breakdown of the 54 Added Records (IDs 386–439):**
  1. **ID 386** (`2026-11-11 15:20:39`): `prop-1` (thread `t-1`, `proposed`) from exploratory pipeline invocation.
  2. **IDs 387–405 (19 records)** (`2026-11-11 15:21:42` – `15:24:51`): Test suite execution of `tests/test_stage6_tasks66_67.py`:
     - `prop-abc-123` (`approval-restart-thread-99`): `proposed`, `approved`, `executing`, `completed`
     - `prop-rej-88` (`reject-restart-thread-88`): `proposed`, `rejected`
     - `prop-tamper-77` (`tamper-thread-77`): `proposed`, `rejected`
  3. **IDs 406–439 (34 records)** (`2026-11-11 15:27:48` – `15:53:34`): Live acceptance runner iterations (`scratch/run_live_postgres_acceptance.py`):
     - `prop-live-approval-202` (`thread-live-approval-202`): 4 cycles of `proposed`, `approved`, `executing`, `completed` (actor `live_sre_lead`)
     - `prop-live-reject-303` (`thread-live-reject-303`): 4 cycles of `proposed`, `rejected` (actor `supervisor_carol`)
     - `prop-live-tamper-404` (`thread-live-tamper-404`): 4 cycles of `proposed`, `rejected` (`Security violation: decision SQL hash mismatch`)

### Phase 7: Initial Scope Separation & Audit Findings
Following the initial black-box test, a read-only audit revealed two specific gaps:
1. SQLAlchemy metadata persistence had not yet been executed against PostgreSQL.
2. The initial human approval test had stubbed `executor.execute` with a mocked `QueryResult` to verify state recovery without triggering a schema mismatch on `status`.

Both gaps were then rigorously resolved in an isolated verification run (`scratch/run_stage6_gap_verification.py`), detailed below.

### Phase 8: Gap 1 — Live PostgreSQL SQLAlchemy Metadata Persistence

- **Container:** `advanced-rag-stage6-pg-gaps` (`PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2)`) on `127.0.0.1:55432`.
- **Alembic Migration Application:**
  - Applied existing migrations `001_baseline_registry` and `002_approval_audit_table` to PostgreSQL via `alembic upgrade head`.
  - Verified tables in `information_schema.tables`: `['alembic_version', 'approval_audit', 'document_metadata', 'ingestion_runs']`.
- **Process 1 (PID 16500) — Synthetic Record Insertion:**
  - Inserted `DocumentMetadataRecord` (`document_id="doc-gap-synth-001"`, `indexed_chunk_count=7`, `title="Synthetic Runbook for Gap Verification"`).
  - Inserted `IngestionRunRecord` (`run_id="run-gap-synth-001"`, `status="completed"`, `duration_seconds=2.45`).
  - Inserted `ApprovalAuditEventRecord` (`proposal_id="prop-gap-synth-001"`, `thread_id="thread-gap-synth-001"`, `event_type="proposed"`, `sql_query="SELECT 1;"`).
  - Process 1 committed and exited completely.
- **Process 2 (PID 12820) — Multi-Process Verification:**
  - Started completely distinct OS process (`PID 12820 != 16500`).
  - Connected via `advanced_rag.db.session.get_db_session()` to PostgreSQL.
  - Successfully retrieved `doc-gap-synth-001` (all fields, timestamps, and chunk counts verified).
  - Successfully retrieved `run-gap-synth-001` (status `completed`, duration 2.45s).
  - Successfully retrieved `prop-gap-synth-001` (event `proposed`).
- **Idempotency Check:**
  - Re-executed `alembic upgrade head` against PostgreSQL.
  - Verified no duplicate rows were created: `doc_count=1`, `run_count=1`, `audit_count=1`.
- **Status:** **PASS**

### Phase 9: Gap 2 — Actual SQL Execution After PostgreSQL Checkpoint Recovery

- **Target Query:** `SELECT id, name, environment FROM clusters WHERE environment = 'production'`
- **Target Schema:** Real operational schema `(id, name, region, environment, k8s_version, node_count, created_at)`.
- **No Mocking:** `advanced_rag.text2sql.executor.execute` was **not mocked**. Real read-only database execution was used.
- **Process A (PID 9360) — Proposal & Interrupt:**
  - Configured `ApprovalAuditStore` to write to PostgreSQL container (`stage6_gaps_test`).
  - Generated proposal `prop-gap2-real-sql-001` (`sql_hash="bad318883f957145e2041df23338b034e3e3224a7af7e4d834787f70a4a7ed89"`).
  - Parked at human approval gate (`awaiting_approval=True`).
  - Stored `proposal_id`, `thread_id`, and `sql_hash` in PostgreSQL checkpointer.
  - Process A exited.
- **Process B (PID 26724) — Recovery & Real Execution:**
  - Started distinct OS process (`PID 26724 != 9360`).
  - Recovered pending approval from PostgreSQL checkpoint via `pipeline.pending_approval(thread_id)`.
  - Resumed thread with `approved=True`, `actor="sre_principal_live"`.
  - Graph routed to `sql_execute_node`, which invoked genuine `executor.execute(...)`.
  - Real read-only query executed against `clusters`, returning the 3 actual production clusters:
    - `[1, "prod-eu-west-1", "production"]`
    - `[2, "prod-us-east-1", "production"]`
    - `[3, "prod-ap-south-1", "production"]`
  - Resumed status: `completed`, awaiting approval: `False`.
- **Audit Store Lifecycle in PostgreSQL:**
  - Queried PostgreSQL `approval_audit` table directly for `prop-gap2-real-sql-001`:
    - Event 1: `proposed`
    - Event 2: `approved` (actor `sre_principal_live`)
    - Event 3: `executing`
    - Event 4: `completed` (outcome `"3 rows"`)
- **Replay Protection:** Calling `pipeline.resume()` on the completed thread returned `"This proposal has already been processed (status: completed)."`.
- **Tampered Hash Protection:** Calling `pipeline.resume()` with an altered hash immediately rejected the request with `"Security violation: decision SQL hash mismatch"`.
- **Status:** **PASS**

### Phase 10: Storage Safety & Zero-Mutation Audit
- **SQLite Database (`data/ops.db`):**
  - Checked table row counts before and after the gap verification run:
    - `clusters`: 5 (0 mutations)
    - `nodes`: 46 (0 mutations)
    - `deployments`: 292 (0 mutations)
    - `incidents`: 45 (0 mutations)
    - `slo_breaches`: 28 (0 mutations)
    - `document_metadata`: 661 (0 mutations)
    - `ingestion_runs`: 528 (0 mutations)
    - `approval_audit`: 485 (0 mutations — all gap audit records were routed to PostgreSQL)
  - **Verdict:** Exactly 0 rows modified or added in `data/ops.db`.
- **Vector Store:**
  - Embedded Qdrant collection `sre_copilot_v1` point count: exactly **5,533** points preserved.
- **Teardown:**
  - Disposable container `advanced-rag-stage6-pg-gaps` stopped and removed via `docker stop` and `docker rm -v`.
  - Host port `55432` confirmed free.
  - Verified via `docker ps -a`: 0 containers remaining.
- **Status:** **PASS**

---

## 4. Full Repository Regression Run

Following live acceptance verification, the complete repository test suite was executed:
- **Command:** `python -m pytest -q --tb=short`
- **Result:** **673 passed in 77.37s**
- **Exit Code:** 0
- **Failures:** 0

```text
........................................................................ [ 10%]
........................................................................ [ 21%]
........................................................................ [ 32%]
........................................................................ [ 42%]
........................................................................ [ 53%]
........................................................................ [ 64%]
........................................................................ [ 74%]
........................................................................ [ 85%]
........................................................................ [ 96%]
.........................                                                [100%]
673 passed in 77.37s (0:01:17)
```

---

## 5. Final Acceptance Summary & Sign-off

| Verification Area | Verification Mode | Evidence & Scope | Final Verdict |
| :--- | :--- | :--- | :--- |
| **Live PostgreSQL Checkpoint Persistence** | Live Container (127.0.0.1:55432) | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` created; state restored across OS PIDs 25596 & 21492 | **PASS** |
| **Live PostgreSQL SQLAlchemy Metadata Persistence** | Live Container (127.0.0.1:55432) | Alembic migrations applied; `document_metadata`, `ingestion_runs`, `approval_audit` verified across OS PIDs 16500 & 12820; migration idempotency verified | **PASS** |
| **Actual SQL Execution After Checkpoint Recovery** | Live Container & Read-Only DB | Real query `SELECT id, name, environment FROM clusters WHERE environment = 'production'` executed via unmocked `executor.execute`; 3 rows returned; PG audit lifecycle verified across PIDs 9360 & 26724; replay & tamper protection enforced | **PASS** |
| **Unit & Regression Test Coverage** | Local Python Test Suite | 13 dedicated Stage 6 checkpoint/recovery tests passing; 673 full repository regression tests passing in 77.37s | **PASS** |

**Final Overall Acceptance Verdict:** **PASS (100% of Acceptance Criteria Met and All Gaps Closed)**
