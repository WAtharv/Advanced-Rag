# Stage 5 — Tasks 58 & 59: Security Remediation Report
## Comprehensive Vulnerability Remediation, Schema Reconciliation & Regression Verification

**Date**: 2026-11-11
**Project**: Enterprise Advanced RAG / Kubernetes SRE Copilot
**Status**: Successfully Remediated & Verified (601 repository-wide tests passing)
**Deliverable**: `docs/STAGE_5_TASKS_58_59_REMEDIATION_REPORT.md`

---

## 1. Executive Summary

Following the independent security audit documented in `docs/STAGE_5_TASKS_58_59_SECURITY_AUDIT.md` (which returned a verdict of **FAIL** due to a PostgreSQL credential fallback and multiple AST validation bypasses), a targeted security remediation batch was executed.

All identified vulnerabilities, AST parsing gaps, function bypasses, and documentation discrepancies have been resolved and independently validated:

1. **PostgreSQL Fail-Closed Guarantee (SEC-01)**: `sql_readonly_url` in `src/advanced_rag/config.py` now strictly fails closed with a clear, safe configuration error if `postgres_dsn` is configured but `postgres_readonly_dsn` is missing. It never falls back to the application read-write credentials.
2. **Rejection of `SELECT ... INTO` (SEC-02)**: Added `exp.Into` to `DANGEROUS_NODE_TYPES` in `src/advanced_rag/text2sql/security.py`. `SELECT ... INTO` attempts across new tables, existing tables, TEMP tables, and inside CTEs are caught and rejected at the AST level.
3. **Rejection of Row Locking Constructs (SEC-03)**: Added `exp.Lock` to `DANGEROUS_NODE_TYPES`. `SELECT ... FOR UPDATE` and `SELECT ... FOR SHARE` queries across root statements, nested subqueries, CTEs, and UNION branches are caught and rejected at the AST level across all dialects.
4. **Closed Function Allowlist for All Invocations (SEC-04)**: Unified function inspection across both `exp.Anonymous` and `exp.Func` subclasses, properly filtering out binary/unary operators (`exp.Binary`, `exp.Connector`, etc.) and enforcing a closed allowlist (`ALLOWED_FUNCTIONS`) alongside dangerous function detection. Table-valued functions like `generate_series` are explicitly blocked.
5. **PostgreSQL Role Script Hardening (SEC-05)**: Updated `scripts/provision_postgres_readonly.sql` with explicit `NOINHERIT`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOBYPASSRLS`, sequence permission revocations, future table/sequence default privilege revocations, and scoped `REVOKE ALL ON TABLE ... FROM PUBLIC` remediation.
6. **Reconciliation of Implementation Report Schema (DOC-01)**: Corrected Section 6 of `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` to match the actual physical SQLite schema (`data/ops.db`) and code allowlist (`security.py`).
7. **Controlled Dependency Versioning**: Pinned `"sqlglot>=30.0.0,<31.0.0"` in `pyproject.toml` to guarantee compatibility with the required AST node classes.

The test suite in `tests/test_stage5_tasks58_59.py` was expanded from 41 to **68 focused tests**, all passing. Full repository regression testing confirmed **601 passed, 0 failed, 0 errors** across the entire codebase.

---

## 2. Detailed Findings, Root Causes & Implemented Fixes

### Finding SEC-01: Critical PostgreSQL Fail-Open Credential Fallback

- **Severity**: **CRITICAL**
- **Root Cause**: `src/advanced_rag/config.py` contained `if self.postgres_dsn: return self.sql_url` in `sql_readonly_url`. If an operator configured `POSTGRES_DSN` for production but omitted `POSTGRES_READONLY_DSN`, Text2SQL silently connected using the application's primary read-write credentials, relying solely on an in-session `SET default_transaction_read_only = ON;`.
- **Exact Fix**: Updated `sql_readonly_url` in `src/advanced_rag/config.py` to raise a `ValueError` when `postgres_dsn` is present without `postgres_readonly_dsn`:
  ```python
  @property
  def sql_readonly_url(self) -> str:
      """SQLAlchemy URL for Text2SQL read-only execution.

      Fails closed if PostgreSQL is configured without a dedicated read-only DSN.
      """
      if self.postgres_readonly_dsn:
          dsn = self.postgres_readonly_dsn
          if dsn.startswith("postgresql://"):
              return "postgresql+psycopg://" + dsn[len("postgresql://"):]
          return dsn
      if self.postgres_dsn:
          raise ValueError(
              "POSTGRES_READONLY_DSN must be explicitly configured when PostgreSQL is used for Text2SQL. "
              "Falling back to application write credentials (POSTGRES_DSN) is strictly forbidden."
          )
      path = self.absolute(self.sqlite_path)
      return f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
  ```
  Updated `get_readonly_engine()` in `src/advanced_rag/text2sql/database.py` to redact credentials and log only the host/database target.
- **Verification**: Verified by `test_postgres_missing_readonly_dsn_fails_closed` and `test_postgres_with_both_dsns_uses_readonly_dsn`.

---

### Finding SEC-02: AST Validation Bypass on `SELECT ... INTO`

- **Severity**: **HIGH**
- **Root Cause**: SQLGlot parses `SELECT ... INTO` as an `exp.Select` statement with an `into=exp.Into(...)` argument. Because `exp.Into` was not listed in `DANGEROUS_NODE_TYPES`, if the target table matched an operational table name (`SELECT * INTO clusters FROM nodes`), AST validation marked the query valid.
- **Exact Fix**: Added `exp.Into` to `DANGEROUS_NODE_TYPES` in `src/advanced_rag/text2sql/security.py`.
- **Verification**: Tested against:
  - `SELECT * INTO new_table FROM clusters`
  - `SELECT * INTO clusters FROM nodes`
  - `SELECT * INTO TEMP t FROM clusters`
  - `WITH cte AS (SELECT * INTO t FROM clusters) SELECT * FROM cte`
  All are strictly rejected with `Forbidden construct 'INTO' detected in query or subquery.`

---

### Finding SEC-03: AST Validation Bypass on Row Locking (`FOR UPDATE` / `FOR SHARE`)

- **Severity**: **HIGH**
- **Root Cause**: In SQLGlot, `FOR UPDATE` and `FOR SHARE` clauses are parsed as `exp.Lock(update=True)` attached to the `Select` node. Because `exp.Lock` was omitted from `DANGEROUS_NODE_TYPES`, locking queries bypassed AST validation across root statements, nested subqueries, CTEs, and UNION branches.
- **Exact Fix**: Added `exp.Lock` to `DANGEROUS_NODE_TYPES` in `src/advanced_rag/text2sql/security.py`.
- **Verification**: Verified by 6 dedicated test cases:
  - `test_ast_rejects_select_for_update_postgres`
  - `test_ast_rejects_select_for_update_sqlite`
  - `test_ast_rejects_select_for_share_postgres`
  - `test_ast_rejects_nested_locking_select`
  - `test_ast_rejects_cte_locking_select`
  - `test_ast_rejects_union_locking_select`

---

### Finding SEC-04: Function Allowlist Bypass on `exp.Func` Subclasses

- **Severity**: **HIGH**
- **Root Cause**: In `security.py`, only `exp.Anonymous` (unrecognized functions) checked `ALLOWED_FUNCTIONS`. Built-in SQLGlot function subclasses (`exp.Func`) only checked `DANGEROUS_FUNCTIONS`, allowing arbitrary built-in functions like `generate_series`, `sqrt`, `cbrt`, etc., to pass validation.
- **Exact Fix**:
  1. Updated `DANGEROUS_FUNCTIONS` to include `nextval`, `setval`, `set_config`, and `pg_advisory_lock`.
  2. Expanded `ALLOWED_FUNCTIONS` with canonical SQLGlot AST names (`timestamp_trunc`, `time_to_str`, `str_to_date`, `str_to_time`, `str_position`, `currentdate`, `currenttimestamp`, `ceil`, `floor`, etc.) while strictly maintaining approved aggregate, math, string, and date capabilities.
  3. Refactored function traversal in `validate_sql_ast()` to inspect both `exp.Anonymous` and `exp.Func`, explicitly skipping operator subclasses (`exp.Binary`, `exp.Connector`, `exp.Unary`, `exp.Predicate`), rejecting dangerous functions, blocking table-valued functions (`generate_series`, `unnest`), and enforcing the closed allowlist:
  ```python
  if (
      isinstance(node, (exp.Func, exp.Anonymous))
      and not isinstance(node, (exp.Binary, exp.Unary, exp.Connector, exp.Predicate))
  ):
      candidates: set[str] = set()
      if isinstance(node, exp.Anonymous):
          candidates.add(node.name.lower())
      else:
          if hasattr(node, "sql_name") and node.sql_name():
              candidates.add(node.sql_name().lower())
          candidates.add(type(node).__name__.lower())
          if hasattr(node, "key") and node.key:
              candidates.add(str(node.key).lower())

      dangerous_match = candidates.intersection(DANGEROUS_FUNCTIONS)
      if dangerous_match:
          fn_name = sorted(dangerous_match)[0]
          return SqlValidationResult(is_valid=False, error=f"Forbidden function '{fn_name}' is not permitted.")

      if any("generateseries" in c or "unnest" in c for c in candidates):
          return SqlValidationResult(is_valid=False, error="Table-valued functions like generate_series are not permitted.")

      if not candidates.intersection(ALLOWED_FUNCTIONS):
          fn_display = sorted(candidates)[0]
          return SqlValidationResult(is_valid=False, error=f"Function '{fn_display}' is not allowed by policy.")
  ```
- **Verification**: Verified by parametrized tests against 10 dangerous functions, `generate_series`, unapproved functions (`sqrt`, `custom_evil_fn`), and approved aggregate/date/string functions.

---

### Finding SEC-05: PostgreSQL Role Script Hardening

- **Severity**: **MEDIUM**
- **Root Cause**: `scripts/provision_postgres_readonly.sql` did not specify `NOINHERIT` or explicit security flags (`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOBYPASSRLS`), did not revoke sequences, and did not address default permissions granted to `PUBLIC` on sensitive tables.
- **Exact Fix**:
  1. Updated role creation with `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`.
  2. Added `REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM text2sql_readonly;`.
  3. Added `ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM text2sql_readonly;`.
  4. Added scoped remediation for inherited public permissions:
     ```sql
     REVOKE ALL ON TABLE document_metadata FROM PUBLIC;
     REVOKE ALL ON TABLE ingestion_runs FROM PUBLIC;
     ```
  5. Added documented DBA preflight inspection queries.
- **Verification**: Verified via `test_postgres_role_script_hardened_privileges`.

---

### Finding DOC-01: Implementation Report Schema Hallucination

- **Severity**: **HIGH** (Documentation Accuracy)
- **Root Cause**: Section 6 of `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` had documented a hallucinated Kubernetes-operator-style schema (`kubernetes_version`, `replicas_desired`, `service_name`, `breach_start`, `severity = 'P0'`), even though `data/ops.db` and `security.py` were already in exact agreement on the real schema.
- **Exact Fix**: Updated Section 6 and Section 8 in `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` with:
  - `clusters`: `id`, `name`, `region`, `environment`, `k8s_version`, `node_count`, `created_at`
  - `nodes`: `id`, `cluster_id`, `name`, `instance_type`, `status`, `cpu_cores`, `memory_gb`, `last_ready_at`
  - `deployments`: `id`, `cluster_id`, `service`, `namespace`, `version`, `deployed_at`, `deployed_by`, `status`, `duration_seconds`, `rolled_back`
  - `incidents`: `id`, `cluster_id`, `service`, `severity` (`sev1`–`sev3`), `title`, `root_cause_category`, `started_at`, `resolved_at`, `duration_minutes`, `customer_impacting`
  - `slo_breaches`: `id`, `cluster_id`, `service`, `slo_name`, `target_pct`, `actual_pct`, `window_start`, `window_end`
  - Replaced stale `'P0'` example with `'sev1'`.
  - Replaced `return self.sql_url` with the `ValueError` fail-closed code snippet.

---

### Finding DEP-01: SQLGlot Dependency Reconciliation

- **Severity**: **LOW** (Dependency Hygiene)
- **Root Cause**: `pyproject.toml` declared `"sqlglot>=25.0.0"`. Older 25.x versions could lack AST node classes or dialect behaviors present in 30.x.
- **Exact Fix**: Updated `pyproject.toml` to `"sqlglot>=30.0.0,<31.0.0"`.

---

## 3. Summary of Files Changed

| File Path | Nature of Change |
| :--- | :--- |
| `src/advanced_rag/config.py` | Updated `sql_readonly_url` to fail closed with `ValueError` when `postgres_dsn` is set without `postgres_readonly_dsn`. |
| `src/advanced_rag/text2sql/database.py` | Updated `get_readonly_engine()` docstring and log redaction. |
| `src/advanced_rag/text2sql/security.py` | Added `exp.Into` and `exp.Lock` to `DANGEROUS_NODE_TYPES`. Added `nextval`, `setval`, `set_config`, `pg_advisory_lock` to `DANGEROUS_FUNCTIONS`. Added canonical SQLGlot functions to `ALLOWED_FUNCTIONS`. Implemented strict closed function validation skipping operators and blocking table-valued functions. |
| `scripts/provision_postgres_readonly.sql` | Hardened role creation (`NOINHERIT`, `NOBYPASSRLS`, etc.), revoked sequences, revoked `PUBLIC` table permissions, added DBA preflight audit query. |
| `pyproject.toml` | Pinned `"sqlglot>=30.0.0,<31.0.0"`. |
| `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` | Reconciled Section 6 schema, Section 8 fail-closed DSN documentation, and CTE example query. |
| `tests/test_stage5_tasks58_59.py` | Added 27 new tests (Part 5) covering all bypass vectors and security requirements. Total suite size: 68 tests. |

---

## 4. Test Verification & Regression Results

### 1. Remediation Test Suite (`tests/test_stage5_tasks58_59.py`)
```bash
.venv\Scripts\pytest tests/test_stage5_tasks58_59.py -v
```
- **Total Tests**: 68
- **Passed**: **68**
- **Failed**: 0
- **Duration**: **4.88s**
- **Exit Code**: 0

### 2. Regression Suites Executed
```bash
.venv\Scripts\pytest tests/test_stage5_task57.py tests/test_text2sql.py tests/test_graph.py tests/test_offline_mode.py -v
```
- **Total Tests**: 82
- **Passed**: **82**
- **Failed**: 0
- **Duration**: **12.82s**
- **Exit Code**: 0

### 3. API Integration Suite (with TestClient loopback outside sandbox)
```bash
.venv\Scripts\pytest tests/test_api.py tests/test_document_api.py -v
```
- **Total Tests**: 32
- **Passed**: **32**
- **Failed**: 0
- **Duration**: **11.84s**
- **Exit Code**: 0

### 4. Full Repository Test Suite
```bash
.venv\Scripts\python -m pytest -q --tb=short
```
- **Previous Baseline**: 574 passed
- **Remediation Baseline**: **601 passed**
- **Failed**: **0**
- **Errors**: **0**
- **Duration**: **51.58s**
- **Exit Code**: 0

---

## 5. PostgreSQL Infrastructure Limitations

As documented honestly throughout this project:
- No live PostgreSQL daemon is active in the local Windows workspace; local execution runs on SQLite (`data/ops.db`).
- All PostgreSQL validation (AST dialect parsing, PostgreSQL date functions, `ILIKE`, `date_trunc`, `NOW()`, and script syntax) has been verified via SQLGlot multi-dialect AST verification, unit tests, and static SQL analysis.
- Full live database verification of `scripts/provision_postgres_readonly.sql` must occur against a deployed PostgreSQL instance during production staging deployment.

---

## 6. Scope Confirmation

In strict compliance with instructions:
- **Task 60 (Timeouts and Cancellation)**: No execution timeout or query cancellation code was added; deferred to Task 60.
- **Task 61 (Approval UI Polish)**: No human approval flow changes were made (`interrupt()` remains mandatory and unbypassed).
- **Task 62 (Result Formatting & SRE Summarization)**: Untouched.
- **Task 63 (Alembic Database Migrations)**: Untouched.
- **Git State**: No `git add`, `git commit`, `git push`, or `git clean` commands were executed.
- **Environment**: `.env` was not modified and credentials were not exposed.

---

## 7. Final Acceptance Status

| Acceptance Requirement | Status | Verification Evidence |
| :--- | :--- | :--- |
| PostgreSQL DSN Fails Closed | **PASS** | `test_postgres_missing_readonly_dsn_fails_closed` |
| `SELECT ... INTO` Rejected Across All Dialects | **PASS** | `test_ast_rejects_select_into_*` (4 tests) |
| `SELECT ... FOR UPDATE / FOR SHARE` Rejected | **PASS** | `test_ast_rejects_select_for_*` (6 tests) |
| Side-Effecting & Dangerous Functions Rejected | **PASS** | `test_ast_rejects_side_effecting_functions` (10 parametrized cases) |
| Table-Valued Functions Blocked | **PASS** | `test_ast_rejects_table_valued_functions` |
| Closed Function Allowlist Enforced on `exp.Func` | **PASS** | `test_ast_rejects_unknown_functions_by_default` |
| Safe Aggregates, Strings & Dates Preserved | **PASS** | `test_ast_accepts_safe_math_string_aggregate_date_functions` |
| PostgreSQL Role Script Hardened | **PASS** | `test_postgres_role_script_hardened_privileges` |
| Physical Schema Reconciled in Documentation | **PASS** | Section 6 in `STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` |
| SQLGlot Dependency Reconciled | **PASS** | `pyproject.toml` pinned to `"sqlglot>=30.0.0,<31.0.0"` |
| Complete Repository Suite Regression | **PASS** | **601 passed in 51.58s** (0 failures, 0 errors) |
