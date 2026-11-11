# Stage 5 — Combined Tasks 60 + 61 Implementation Report
## SQL Execution Limits & Human Approval Workflow Improvements

**Date**: 2026-11-11
**Project**: Enterprise Advanced RAG / Kubernetes SRE Copilot
**Status**: COMPLETE
**Repository Test Suite**: 619 passed, 0 failed (100% pass rate)

---

## 1. Executive Summary

This report documents the implementation of **Stage 5 Tasks 60 and 61**:
- **Task 60 (SQL Execution Timeouts & Resource Limits)**: Enforces strict query deadlines, result cell ceilings, row limits, and connection concurrency bounds across both SQLite and PostgreSQL execution engines.
- **Task 61 (Human Approval and Rejection Workflow Improvements)**: Enforces an explicit 7-state approval lifecycle, configurable proposal expiration (TTL), cryptographic SHA-256 SQL hash binding, tamper detection, replay prevention, operator actor/reason capture, an in-process audit history store, and enriched API & Web UI approval interfaces.

All changes build directly on top of the accepted Task 57, 58, and 59 foundations without breaking existing workflows or degrading the 601 baseline tests.

---

## 2. Prerequisite Audit Verification (Tasks 58 & 59)

Before beginning implementation, the prerequisite audit was verified:
- **Audit Document**: `docs/STAGE_5_TASKS_58_59_FINAL_ACCEPTANCE_AUDIT.md`
- **Verdict**: **PASS**
- **Resolved Findings**:
  - PostgreSQL fail-closed configuration verified: `sql_readonly_url` raises a `ValueError` if `POSTGRES_READONLY_DSN` is not explicitly set; write credentials are never exposed or reused for Text2SQL.
  - SQL AST validation verified: `exp.Into` (SELECT ... INTO), `exp.Lock` (FOR UPDATE / FOR SHARE), and dangerous functions/procedures (`generate_series`, `pg_sleep`, `load_extension`, etc.) are blocked.
  - Operational allowlists confirmed: strictly bounded to 5 operational tables (`clusters`, `nodes`, `deployments`, `incidents`, `slo_breaches`).

---

## 3. Task 60 — SQL Execution Timeouts

Execution timeouts are enforced at the database driver level and transaction level to prevent denial-of-service, unindexed table scans, and expensive joins from monopolizing database connections.

### SQLite Progress Handler Deadline Enforcement
- SQLite lacks a native `SET statement_timeout` command. Instead, execution deadlines are enforced using the native Python `sqlite3.Connection.set_progress_handler` API.
- In `src/advanced_rag/text2sql/executor.py`, before query execution on SQLite:
  ```python
  raw_conn = getattr(conn.connection, "dbapi_connection", None)
  if raw_conn is not None and hasattr(raw_conn, "set_progress_handler"):
      dbapi_conn = raw_conn
      deadline = time.monotonic() + timeout

      def _sqlite_progress() -> int:
          if time.monotonic() > deadline:
              return 1  # Aborts query with sqlite3.OperationalError: interrupted
          return 0

      dbapi_conn.set_progress_handler(_sqlite_progress, 100)
  ```
- **Handler Cleanup Guarantee**: In the `finally` block of `executor.execute()`, `dbapi_conn.set_progress_handler(None, 0)` is guaranteed to run. This prevents leftover handlers from interrupting subsequent queries executed on pooled connections.
- **Exception Mapping**: Any `sqlite3.OperationalError: interrupted` or timeout-related error is caught and mapped to `SqlTimeoutError(SqlExecutionError)`.

### PostgreSQL Transaction-Local Timeout Enforcement
- When executing against PostgreSQL (detected via dialect or configured DSN), transaction-local statements are issued immediately inside the active transaction:
  ```sql
  SET LOCAL statement_timeout = '<N>ms';
  SET LOCAL transaction_read_only = on;
  ```
- Database-level default connection timeout is preserved at `15s`.
- PostgreSQL query cancellation / statement timeout errors are mapped to `SqlTimeoutError`.

### Configuration
- `Settings.sql_timeout_seconds` in `src/advanced_rag/config.py` supports both integer and fractional float values (default: `15.0` seconds).

---

## 4. Task 60 — Database Resource Limits

To protect memory and prevent oversized payloads from overwhelming the LLM prompt or UI:

1. **Row Limit Clamping**:
   - `settings.sql_row_limit` (default: 50 rows).
   - Queries fetch up to `sql_row_limit + 1` rows to detect truncation accurately.
   - If more rows exist, the result is truncated to `sql_row_limit` and `QueryResult.truncated` is set to `True`.

2. **Result Cell Limit Clamping**:
   - Configured via `Settings.sql_max_result_cells` (default: `5000` cells).
   - If total cells (`rows * columns`) exceeds `sql_max_result_cells`, `rows` are clamped to `max(1, sql_max_result_cells // len(columns))` and `truncated` is marked `True`.

3. **Concurrency Semaphore**:
   - Configured via `Settings.sql_max_concurrent_queries` (default: `5` concurrent executions).
   - Managed via a process-wide `threading.BoundedSemaphore`.
   - If semaphore acquisition times out (5.0s wait ceiling), `SqlResourceExceededError` is raised, alerting the client to retry shortly without queuing indefinitely.

---

## 5. Task 60 — Connection and Transaction Lifecycle

- **Read-Only Enforced**:
  - SQLite connections use read-only URI mode (`file:...?...mode=ro`).
  - PostgreSQL transactions enforce `SET LOCAL transaction_read_only = on`.
- **Always Rollback**:
  - Transactions are initiated with `conn.begin()` and unconditionally rolled back in the `finally` block (`transaction.rollback()`), preventing write attempts, dirty reads, or lingering locks.
- **Engine Independence**:
  - The read-only execution engine (`get_readonly_engine()`) remains completely separate from the application write engine (`get_engine()`). Read-only limits, progress handlers, and connection timeouts never leak into metadata or ingestion pipelines.

---

## 6. Task 61 — Human Approval State Machine

`ApprovalStatus` is established as an explicit `StrEnum` in `src/advanced_rag/models.py`:

```
          ┌─────────────┐
          │   PENDING   │ (Proposal generated)
          └──────┬──────┘
                 │
       ┌─────────┼─────────┐
       │         │         │
       ▼         ▼         ▼
  ┌────────┐┌────────┐┌─────────┐
  │APPROVED││REJECTED││ EXPIRED │ (Decision or TTL expiry)
  └───┬────┘└────────┘└─────────┘
      │
      ▼
┌───────────┐
│ EXECUTING │ (Pre-execution gate passed)
└─────┬─────┘
      │
 ┌────┴────┐
 ▼         ▼
┌─────────┐┌────────┐
│COMPLETED││ FAILED │ (Execution outcome)
└─────────┘└────────┘
```

The `SqlProposal` model carries:
- `proposal_id: str` (UUID4 hex)
- `sql_hash: str` (SHA-256)
- `status: ApprovalStatus`
- `requested_at: str` (ISO 8601 UTC)
- `expires_at: str` (ISO 8601 UTC)
- `decided_at: str | None`
- `actor: str | None`
- `rejection_reason: str | None`
- `execution_started_at: str | None`
- `execution_completed_at: str | None`

---

## 7. Task 61 — SQL Expiry & Lifetime Controls

1. **Configurable TTL**:
   - `Settings.approval_expiry_seconds` (default: `300` seconds / 5 minutes).
2. **Generation Timestamping**:
   - `sql_generate_node` stamps `requested_at` and `expires_at = requested_at + timedelta(seconds=settings.approval_expiry_seconds)`.
3. **Dual-Gate Expiry Checks**:
   - **Gate 1 (`sql_approval_node`)**: When a human decides, the proposal's `expires_at` is compared against current UTC time. If expired, status becomes `ApprovalStatus.EXPIRED`, execution is refused, and the user is prompted to submit a fresh question.
   - **Gate 2 (`sql_execute_node`)**: Right before database execution, `expires_at` is checked again to eliminate race conditions between approval and execution.

---

## 8. Task 61 — Cryptographic Binding & Tamper Detection

1. **SHA-256 SQL Hashing**:
   - Implemented in `executor.compute_sql_hash(sql: str) -> str`.
   - Normalizes whitespace, strips trailing semicolons, and computes a deterministic SHA-256 digest.
2. **Tamper Detection**:
   - In `sql_approval_node`:
     - Compares `compute_sql_hash(proposal.sql)` with stored `proposal.sql_hash`.
     - Compares incoming `resumed_hash` (if provided by client) against `proposal.sql_hash`.
     - Rejects any mismatch as a security violation (`ApprovalStatus.REJECTED`) and aborts execution.
3. **Pre-Execution AST Re-validation**:
   - In `sql_execute_node`, `executor.execute()` re-invokes `validate_sql_ast()` immediately before database connection acquisition, guaranteeing that even if memory was altered, no disallowed tables, columns, or statement types can run.
4. **Replay Prevention**:
   - Proposals in `ApprovalStatus.COMPLETED` or `ApprovalStatus.EXECUTING` reject re-execution attempts, preventing replay attacks.

---

## 9. Task 61 — Rejection Workflow & Handling

- When an operator rejects a query (`approved=False`):
  - Status is set to `ApprovalStatus.REJECTED`.
  - Operator identity (`actor`) and optional `reason` (e.g. "Sensitive financial data requested") are recorded in the proposal and audit store.
  - The pipeline transitions via `after_approval` directly to `guardrail_output` with a polite explanation:
    `"The query was rejected: <rejection_reason>"`.
  - Database execution (`sql_execute_node`) is completely bypassed.

---

## 10. Task 61 — Approval Audit History & Persistence

Implemented in `src/advanced_rag/text2sql/audit.py`:
- `ApprovalAuditRecord`: Dataclass tracking `proposal_id`, `thread_id`, `sql_hash`, `sql_query`, `tables`, `status`, `requested_at`, `expires_at`, `decided_at`, `actor`, `rejection_reason`, `execution_started_at`, `execution_completed_at`, `execution_outcome`.
- `ApprovalAuditStore`: Thread-safe, lock-protected in-process store with:
  - `record_proposal()`
  - `record_decision()`
  - `record_execution_start()`
  - `record_execution_end()`
  - `get(proposal_id)`
  - `get_by_thread(thread_id)` / `list_by_thread(thread_id)`
  - `list_recent(limit)`
- Global singleton accessed via `get_audit_store()`.

---

## 11. API and Frontend Integration

### FastAPI Endpoints (`src/advanced_rag/api/main.py`)
1. **`POST /ask`**:
   - When `awaiting_approval=True`, response includes full proposal details (`proposal_id`, `sql_hash`, `status`, `requested_at`, `expires_at`, `tables`, `dialect`).
2. **`POST /approve`**:
   - Extended `ApproveRequest`:
     ```python
     class ApproveRequest(BaseModel):
         thread_id: str
         approved: bool
         proposal_id: str | None = None
         sql_hash: str | None = None
         reason: str | None = None
         actor: str | None = None
     ```
   - Forwards metadata into `pipeline.resume()` while maintaining 100% backward compatibility for callers passing only `{thread_id, approved}`.
3. **`GET /pending/{thread_id}`**:
   - Returns the active interrupt payload including `proposal_id`, `sql_hash`, `expires_at`, `dialect`, and `tables`.
4. **`GET /approval/audit`**:
   - Returns recent audit records with optional `limit` (default 50) and `thread_id` filtering.

### Frontend Web UI (`ui/web/app.js`)
- **Metadata Badges**: Renders badges for Dialect, Allowed Tables, Expiration timestamp, and SHA-256 hash preview (`hash: abc123def4…`).
- **Rejection Reason Input**: Operator input field for optional notes or rejection rationale.
- **Secure Approval Submission**: Passes `proposal_id`, `sql_hash`, `actor: "web_operator"`, and `reason` when making POST requests to `/approve`.

---

## 12. Files Modified and Created

| File | Change Type | Summary of Changes |
| :--- | :--- | :--- |
| `src/advanced_rag/config.py` | Modified | Added `sql_max_result_cells` (5000), `sql_max_concurrent_queries` (5), `approval_expiry_seconds` (300); changed `sql_timeout_seconds` to `float = 15.0`. |
| `src/advanced_rag/models.py` | Modified | Added `ApprovalStatus` enum; enriched `SqlProposal` with lifecycle, hash, expiry, actor, and audit fields. |
| `src/advanced_rag/text2sql/audit.py` | **Created** | Created `ApprovalAuditRecord` and `ApprovalAuditStore` thread-safe singleton. |
| `src/advanced_rag/text2sql/executor.py` | Modified | Added `SqlTimeoutError`, `SqlResourceExceededError`; added `compute_sql_hash()`; implemented concurrency semaphore, SQLite progress handler, PG statement timeout, and cell truncation. |
| `src/advanced_rag/graph/nodes.py` | Modified | Updated `sql_generate_node` with hash/audit recording; updated `sql_approval_node` with expiry, hash verification, replay check; updated `sql_execute_node` with pre-execution expiry and timeout handling. |
| `src/advanced_rag/graph/pipeline.py` | Modified | Extended `resume()` with optional `reason`, `actor`, `sql_hash`, `proposal_id`; enriched `_to_response()` proposal serialization. |
| `src/advanced_rag/api/main.py` | Modified | Enriched `ApproveRequest`; updated `/approve`; added `GET /approval/audit` endpoint. |
| `ui/web/app.js` | Modified | Added metadata badges (dialect, tables, expires_at, hash), rejection input, and metadata dispatch on approval/rejection. |
| `tests/test_stage5_tasks60_61.py` | **Created** | 18 comprehensive tests verifying timeouts, resource limits, hash checks, lifecycle states, audit store, and API endpoints. |

---

## 13. Comprehensive Test Results

### 1. Focused Task 60 & 61 Suite
Command: `.venv\Scripts\pytest tests/test_stage5_tasks60_61.py -v`
```
tests/test_stage5_tasks60_61.py::test_sqlite_query_success_under_timeout PASSED
tests/test_stage5_tasks60_61.py::test_sqlite_progress_handler_interrupt_on_timeout PASSED
tests/test_stage5_tasks60_61.py::test_sqlite_progress_handler_cleanup PASSED
tests/test_stage5_tasks60_61.py::test_postgres_statement_timeout_applied PASSED
tests/test_stage5_tasks60_61.py::test_resource_limits_cell_truncation PASSED
tests/test_stage5_tasks60_61.py::test_concurrency_semaphore_bounds_execution PASSED
tests/test_stage5_tasks60_61.py::test_application_write_engine_independence PASSED
tests/test_stage5_tasks60_61.py::test_sql_hash_computation_deterministic PASSED
tests/test_stage5_tasks60_61.py::test_proposal_initialization_with_lifecycle_fields PASSED
tests/test_stage5_tasks60_61.py::test_approval_node_expired_proposal PASSED
tests/test_stage5_tasks60_61.py::test_approval_node_sql_tamper_rejected PASSED
tests/test_stage5_tasks60_61.py::test_approval_node_resumed_hash_mismatch_rejected PASSED
tests/test_stage5_tasks60_61.py::test_approval_node_replay_prevention PASSED
tests/test_stage5_tasks60_61.py::test_approval_node_explicit_rejection_with_reason PASSED
tests/test_stage5_tasks60_61.py::test_sql_execute_node_timeout_handling PASSED
tests/test_stage5_tasks60_61.py::test_approval_audit_store_tracking PASSED
tests/test_stage5_tasks60_61.py::test_api_approve_with_metadata PASSED
tests/test_stage5_tasks60_61.py::test_api_approval_audit_endpoint PASSED

============================= 18 passed in 6.50s ==============================
```

### 2. Regression Test Suite
Command: `.venv\Scripts\pytest tests/test_stage5_tasks58_59.py tests/test_stage5_task57.py tests/test_text2sql.py tests/test_api.py tests/test_graph.py tests/test_offline_mode.py -v`
```
============================ 162 passed in 12.83s =============================
```

### 3. Full Repository Test Suite
Command: `python -m pytest -q --tb=short`
```
........................................................................ [ 11%]
........................................................................ [ 23%]
........................................................................ [ 34%]
........................................................................ [ 46%]
........................................................................ [ 58%]
........................................................................ [ 69%]
........................................................................ [ 81%]
........................................................................ [ 93%]
...........................................                              [100%]
619 passed in 47.70s
```

---

## 14. Dialect & Security Invariants Preserved

- **Operational Database Integrity**: The 5 operational tables (`clusters`, `nodes`, `deployments`, `incidents`, `slo_breaches`) remain unmodified.
- **Fail-Closed Security**: Both SQLite and PostgreSQL connections fail closed if read-only DSN configuration is missing or invalid.
- **AST Allowlist Enforcement**: All AST validation gates from Tasks 58 and 59 remain strictly active before and immediately prior to query execution.
- **Zero Credentials Exposed**: Connection strings and credentials remain redacted in logs, exceptions, and audit records.

---

## 15. Backward Compatibility & Operational Safety

- Existing API clients that send `{ "thread_id": "...", "approved": true }` continue to function identically; optional fields (`proposal_id`, `sql_hash`, `reason`, `actor`) default safely.
- Older proposal formats without audit fields automatically receive valid defaults during Pydantic deserialization.
- No Git write operations, destructive file deletions, or `.env` modifications were performed during implementation.

---

## 16. Readiness for Stage 5 Task 62 & Next Steps

Stage 5 Tasks 60 and 61 are complete and verified. The codebase is now prepared for:
- **Task 62**: Text2SQL Result Formatting, SRE Answer Summarization & Empty Result Diagnostics.
- **Task 63**: Operational Database Migrations (Alembic) & PostgreSQL Schema Sync.
