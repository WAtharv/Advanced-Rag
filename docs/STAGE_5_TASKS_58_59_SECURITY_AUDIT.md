# Stage 5 — Tasks 58 & 59: Security and Schema Consistency Audit
## SQL AST Validation, Database-Level Read-Only Permissions & Allowlists

**Audit Date**: 2026-11-11
**Auditor**: Antigravity Independent Security & Verification Engine
**Project**: Enterprise Advanced RAG / Kubernetes SRE Copilot
**Subject**: Stage 5 Tasks 58 & 59 Implementation & Security Boundary
**Target File**: `docs/STAGE_5_TASKS_58_59_SECURITY_AUDIT.md`
**Audit Scope**: Strict Read-Only Inspection (No code, test, schema, config, or Git changes)

---

## 1. Executive Verdict

### **FAIL**

While the local SQLite implementation correctly isolates read-only queries at the C-driver level and 574 tests pass across the repository, this audit identifies **two critical security vulnerabilities**, **two significant AST validation gaps**, and a **major documentation hallucination** in the Tasks 58/59 deliverables:

1. **CRITICAL: PostgreSQL Fail-Open Credential Fallback**:
   In `src/advanced_rag/config.py`, when `postgres_readonly_dsn` is omitted but `postgres_dsn` is present, `sql_readonly_url` silently falls back to `self.sql_url` (the primary application read-write connection string). `get_readonly_engine()` connects using full application write credentials rather than failing closed. A simple session-level `SET default_transaction_read_only = ON;` is the sole protection applied, which does not prevent read access to internal tables (`document_metadata`, `ingestion_runs`) and can be overridden within a transaction.
2. **HIGH RISK: AST Validation Bypass on `SELECT ... INTO`**:
   `sqlglot.expressions.Into` (`exp.Into`) was omitted from `DANGEROUS_NODE_TYPES` in `src/advanced_rag/text2sql/security.py`. Queries such as `SELECT * INTO clusters FROM nodes` parse as valid SELECT statements and bypass AST validation.
3. **HIGH RISK: AST Validation Pass-Through on Row Locking (`FOR UPDATE` / `FOR SHARE`)**:
   `sqlglot.expressions.Lock` (`exp.Lock`) was omitted from `DANGEROUS_NODE_TYPES`. `SELECT * FROM clusters FOR UPDATE` is accepted as valid by AST validation on both SQLite and PostgreSQL dialects.
4. **MEDIUM RISK: Function Allowlist Bypassed for SQLGlot `exp.Func` Classes**:
   In `security.py`, only `exp.Anonymous` (unrecognized functions) checks `if fn_name not in ALLOWED_FUNCTIONS`. Built-in SQLGlot function subclasses (`exp.Func`, such as `exp.ExplodingGenerateSeries`, `exp.Sqrt`, etc.) only check `DANGEROUS_FUNCTIONS` and bypass the allowlist.
5. **DOCUMENTATION ANOMALY: Implementation Report Contains Hallucinated Schema**:
   The code (`security.py`, `database.py`, `schema.py`) and live SQLite database (`data/ops.db`) are mutually consistent and correct. However, `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` (Section 6) documented a completely fictional, Kubernetes-operator-style schema (`kubernetes_version`, `replicas_desired`, `service_name`, `breach_start`, etc.) that does not exist in the database or the code.

---

## 2. Critical and High-Risk Findings

### Summary Findings Matrix

| Finding ID | Severity | Area | Status | Description |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | **CRITICAL** | Configuration / DSN | **VULNERABLE** | `sql_readonly_url` falls back to read-write `postgres_dsn` when `postgres_readonly_dsn` is unset, violating the fail-closed mandate. |
| **SEC-02** | **HIGH** | AST Validation | **VULNERABLE** | `SELECT ... INTO <allowed_table>` is accepted as valid because `exp.Into` is not in `DANGEROUS_NODE_TYPES`. |
| **SEC-03** | **HIGH** | AST Validation | **VULNERABLE** | `SELECT ... FOR UPDATE` and `FOR SHARE` pass AST validation because `exp.Lock` is not in `DANGEROUS_NODE_TYPES`. |
| **SEC-04** | **MEDIUM** | AST Validation | **INCOMPLETE** | Built-in SQLGlot functions (`exp.Func`) bypass `ALLOWED_FUNCTIONS` check, only verifying `DANGEROUS_FUNCTIONS`. |
| **DOC-01** | **HIGH** | Documentation | **INCORRECT** | Section 6 of `STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` documented a hallucinated schema instead of actual code/DB schemas. |
| **SEC-05** | **LOW** | Role Script | **QUALIFIED** | `scripts/provision_postgres_readonly.sql` lacks `NOINHERIT` and does not revoke `PUBLIC` role permissions on default schemas. |

---

## 3. PostgreSQL Read-Only Connection & DSN Fallback Analysis

### Code Inspection

In `src/advanced_rag/config.py` (lines 213–224):
```python
@property
def sql_readonly_url(self) -> str:
    """SQLAlchemy URL for Text2SQL read-only execution."""
    if self.postgres_readonly_dsn:
        dsn = self.postgres_readonly_dsn
        if dsn.startswith("postgresql://"):
            return "postgresql+psycopg://" + dsn[len("postgresql://"):]
        return dsn
    if self.postgres_dsn:
        return self.sql_url  # <--- CRITICAL SECURITY VULNERABILITY
    path = self.absolute(self.sqlite_path)
    return f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
```

In `src/advanced_rag/text2sql/database.py` (lines 234–259):
```python
def get_readonly_engine(settings: Settings | None = None) -> Engine:
    ...
    settings = settings or get_settings()
    url = settings.sql_readonly_url
    logger.info("Operations read-only database: %s", url.split("@")[-1])

    connect_args: dict[str, Any] = {}
    if not settings.postgres_dsn:
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if not settings.postgres_dsn:
        @event.listens_for(engine, "connect")
        def _set_sqlite_readonly(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only = ON;")
            cursor.close()
    else:
        @event.listens_for(engine, "connect")
        def _set_postgres_readonly(dbapi_connection, connection_record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET default_transaction_read_only = ON;")

    _readonly_engine = engine
```

### Vulnerability Analysis

1. **Fail-Open Fallback**:
   When an administrator configures `POSTGRES_DSN` (for production or staging) but omits `POSTGRES_READONLY_DSN`, `settings.sql_readonly_url` evaluates directly to `self.sql_url`.
   Diagnostic execution confirmed:
   ```
   When postgres_readonly_dsn is None:
     s1.sql_url host/db: db.example.com:5432/proddb
     s1.sql_readonly_url host/db: db.example.com:5432/proddb
     Are URLs identical? True
   ```
   The engine connects to PostgreSQL authenticated as the application user (typically an owner or read-write role such as `postgres` or `app_admin`).
2. **Inadequacy of `SET default_transaction_read_only = ON`**:
   The only protection applied is an event listener executing `SET default_transaction_read_only = ON;`.
   - In PostgreSQL, session parameters configured via `SET` can be altered within the same session by issuing `SET default_transaction_read_only = OFF;` or `BEGIN READ WRITE;` unless table-level role permissions forbid writes.
   - More critically, table privileges on the application user encompass **all** tables, including `document_metadata` and `ingestion_runs`. If an AST bypass occurs, the database will happily execute `SELECT * FROM document_metadata`, completely failing to protect internal data at the database layer.
3. **Requirement Breach**:
   The mandatory architectural requirement is:
   > *"Text2SQL PostgreSQL execution must fail closed if a dedicated read-only DSN is missing. It must never silently fall back to the application write DSN."*
   The current implementation fails open.

---

## 4. Physical Database Schema vs Allowlists Verification

A comprehensive audit was performed comparing the live database (`data/ops.db`), the database DDL and metadata (`src/advanced_rag/text2sql/database.py`), the AST security allowlist (`src/advanced_rag/text2sql/security.py`), and the previous audit and implementation reports.

### Detailed Table Comparison

#### 1. `clusters`
- **Actual Physical Schema (`data/ops.db`)**:
  `id` (INTEGER, PK), `name` (VARCHAR 64), `region` (VARCHAR 32), `environment` (VARCHAR 16), `k8s_version` (VARCHAR 16), `node_count` (INTEGER), `created_at` (DATE). Total: 7 columns.
- **Task 57 Documentation (`database.py` / `STAGE_5_TASK_57_INDEPENDENT_AUDIT.md`)**:
  Documented as: `id`, `name`, `region`, `environment`, `k8s_version`, `node_count`, `created_at`.
- **Task 58/59 Code Allowlist (`security.py`)**:
  `OPERATIONAL_COLUMNS["clusters"] = {"id", "name", "region", "environment", "k8s_version", "node_count", "created_at"}`.
  *(Exact match with physical database)*.
- **Task 58/59 Implementation Report Text (`STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` Section 6)**:
  `{"id", "name", "environment", "region", "kubernetes_version", "provider", "status", "node_count", "created_at", "updated_at"}`.
  - `kubernetes_version`: **INCORRECT**. Actual column is `k8s_version`.
  - `provider`, `status`, `updated_at`: **INCORRECT / FICTIONAL**. Do not exist in physical schema.

#### 2. `nodes`
- **Actual Physical Schema (`data/ops.db`)**:
  `id` (INTEGER, PK), `cluster_id` (INTEGER, FK), `name` (VARCHAR 64), `instance_type` (VARCHAR 32), `status` (VARCHAR 16), `cpu_cores` (INTEGER), `memory_gb` (INTEGER), `last_ready_at` (DATE). Total: 8 columns.
- **Task 57 Documentation**:
  Documented as: `id`, `cluster_id`, `name`, `instance_type`, `status`, `cpu_cores`, `memory_gb`, `last_ready_at`.
- **Task 58/59 Code Allowlist (`security.py`)**:
  `OPERATIONAL_COLUMNS["nodes"] = {"id", "cluster_id", "name", "instance_type", "status", "cpu_cores", "memory_gb", "last_ready_at"}`.
  *(Exact match with physical database)*.
- **Task 58/59 Implementation Report Text**:
  `{"id", "cluster_id", "name", "role", "instance_type", "capacity_type", "zone", "status", "cpu_allocatable_cores", "memory_allocatable_gb", "kubelet_version", "created_at", "updated_at"}`.
  - `role`, `capacity_type`, `zone`, `cpu_allocatable_cores`, `memory_allocatable_gb`, `kubelet_version`, `created_at`, `updated_at`: **INCORRECT / FICTIONAL**.
  - `cpu_cores`, `memory_gb`, `last_ready_at`: Omitted from report text despite existing in code and DB.

#### 3. `deployments`
- **Actual Physical Schema (`data/ops.db`)**:
  `id` (INTEGER, PK), `cluster_id` (INTEGER, FK), `service` (VARCHAR 64), `namespace` (VARCHAR 64), `version` (VARCHAR 32), `deployed_at` (DATE), `deployed_by` (VARCHAR 64), `status` (VARCHAR 16), `duration_seconds` (INTEGER), `rolled_back` (INTEGER). Total: 10 columns.
- **Task 57 Documentation**:
  Documented as: `id`, `cluster_id`, `service`, `namespace`, `version`, `deployed_at`, `deployed_by`, `status`, `duration_seconds`, `rolled_back`.
- **Task 58/59 Code Allowlist (`security.py`)**:
  `OPERATIONAL_COLUMNS["deployments"]` matches the physical columns exactly.
- **Task 58/59 Implementation Report Text**:
  `{"id", "cluster_id", "namespace", "name", "replicas_desired", "replicas_available", "image", "status", "created_at", "updated_at"}`.
  - `service`, `version`, `deployed_at`, `deployed_by`, `duration_seconds`, `rolled_back`: Omitted from report text.
  - `name`, `replicas_desired`, `replicas_available`, `image`, `created_at`, `updated_at`: **INCORRECT / FICTIONAL**.

#### 4. `incidents`
- **Actual Physical Schema (`data/ops.db`)**:
  `id` (INTEGER, PK), `cluster_id` (INTEGER, FK), `service` (VARCHAR 64), `severity` (VARCHAR 8), `title` (VARCHAR 200), `root_cause_category` (VARCHAR 48), `started_at` (DATE), `resolved_at` (DATE), `duration_minutes` (INTEGER), `customer_impacting` (INTEGER). Total: 10 columns.
- **Task 57 Documentation**:
  Documented as: `id`, `cluster_id`, `service`, `severity`, `title`, `root_cause_category`, `started_at`, `resolved_at`, `duration_minutes`, `customer_impacting`.
- **Task 58/59 Code Allowlist (`security.py`)**:
  `OPERATIONAL_COLUMNS["incidents"]` matches the physical columns exactly.
- **Task 58/59 Implementation Report Text**:
  `{"id", "incident_number", "title", "severity", "status", "cluster_id", "service_name", "root_cause", "impact_summary", "started_at", "resolved_at", "created_at", "updated_at"}`.
  - `service` vs `service_name`: Code and DB use `service`. `service_name` is **INCORRECT**.
  - `customer_impacting`: Present in DB and code (integer boolean 0/1); omitted in report text.
  - `incident_number`, `status`, `root_cause`, `impact_summary`, `created_at`, `updated_at`: **INCORRECT / FICTIONAL**.
- **Enum & Severity Conventions**:
  - Live data values in `incidents.severity` are `'sev1'`, `'sev2'`, `'sev3'` (sev1 highest severity). The implementation report example used `severity = 'P0'` which is incorrect and yields 0 rows on `ops.db`.
  - `customer_impacting` is stored as an integer flag (`0` or `1`).

#### 5. `slo_breaches`
- **Actual Physical Schema (`data/ops.db`)**:
  `id` (INTEGER, PK), `cluster_id` (INTEGER, FK), `service` (VARCHAR 64), `slo_name` (VARCHAR 48), `target_pct` (NUMERIC 5,2), `actual_pct` (NUMERIC 5,2), `window_start` (DATE), `window_end` (DATE). Total: 8 columns.
- **Task 57 Documentation**:
  Documented as: `id`, `cluster_id`, `service`, `slo_name`, `target_pct`, `actual_pct`, `window_start`, `window_end`.
- **Task 58/59 Code Allowlist (`security.py`)**:
  `OPERATIONAL_COLUMNS["slo_breaches"]` matches the physical columns exactly.
- **Task 58/59 Implementation Report Text**:
  `{"id", "slo_name", "service_name", "target_percentage", "actual_percentage", "breach_start", "breach_end", "error_budget_lost", "incident_id", "created_at", "updated_at"}`.
  - `window_start`, `window_end` vs `breach_start`, `breach_end`: Code and DB use `window_start` and `window_end`.
  - `service` vs `service_name`: Code and DB use `service`.
  - `target_pct`, `actual_pct` vs `target_percentage`, `actual_percentage`: Code and DB use `target_pct` and `actual_pct`.
  - `error_budget_lost`, `incident_id`, `created_at`, `updated_at`: **INCORRECT / FICTIONAL**.

### Schema Consistency Conclusion
The actual source code (`src/advanced_rag/text2sql/security.py`, `database.py`, `schema.py`) and the physical database (`data/ops.db`) are **100% synchronized and correct**. The discrepancy was introduced purely as documentation hallucination in `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md` Section 6.

---

## 5. AST Security Bypass Audit & Test Matrix

Diagnostic test script `scratch/test_ast_security_bypass.py` was executed across 19 syntax variations against `sqlglot==30.31.0` and `validate_sql_ast()`.

### Diagnostic Test Matrix

| # | SQL Test Case | Target Dialect | SQLGlot AST Node Type | AST Validation Result | DB Permission Protection | Final Security Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `SELECT * INTO new_table FROM clusters` | PostgreSQL | `Select` (with `Into`) | **REJECTED** (Unknown table `new_table`) | Blocked (Table does not exist) | **PROTECTED** (By table allowlist) |
| 2 | `SELECT * INTO clusters FROM nodes` | PostgreSQL | `Select` (with `Into`) | **ACCEPTED (is_valid=True)** | Blocked if read-only DSN; **EXECUTED if fallback DSN** | **VULNERABLE (AST Bypass)** |
| 3 | `SELECT * FROM clusters FOR UPDATE` | PostgreSQL | `Select` (with `Lock`) | **ACCEPTED (is_valid=True)** | Blocked in read-only transaction | **VULNERABLE (AST Bypass)** |
| 4 | `SELECT * FROM clusters FOR UPDATE` | SQLite | `Select` (with `Lock`) | **ACCEPTED (is_valid=True)** | Blocked (SQLite syntax error) | **VULNERABLE (AST Bypass)** |
| 5 | `SELECT * FROM clusters FOR SHARE` | PostgreSQL | `Select` (with `Lock`) | **ACCEPTED (is_valid=True)** | Blocked in read-only transaction | **VULNERABLE (AST Bypass)** |
| 6 | `SELECT nextval('my_seq') FROM clusters` | PostgreSQL | `Select` (with `Anonymous`) | **REJECTED** ("not allowed by policy") | N/A | **PROTECTED** |
| 7 | `SELECT setval('my_seq', 100) FROM clusters` | PostgreSQL | `Select` (with `Anonymous`) | **REJECTED** ("not allowed by policy") | N/A | **PROTECTED** |
| 8 | `SELECT set_config('work_mem', '1GB', false) FROM clusters` | PostgreSQL | `Select` (with `Anonymous`) | **REJECTED** ("not allowed by policy") | N/A | **PROTECTED** |
| 9 | `SELECT pg_advisory_lock(12345) FROM clusters` | PostgreSQL | `Select` (with `Anonymous`) | **REJECTED** ("not allowed by policy") | N/A | **PROTECTED** |
| 10 | `SELECT load_extension('evil.so') FROM clusters` | SQLite | `Select` (with `Anonymous`) | **REJECTED** (In `DANGEROUS_FUNCTIONS`) | SQLite driver disabled | **PROTECTED** |
| 11 | `SELECT readfile('/etc/passwd') FROM clusters` | SQLite | `Select` (with `Anonymous`) | **REJECTED** (In `DANGEROUS_FUNCTIONS`) | N/A | **PROTECTED** |
| 12 | `SELECT writefile('/tmp/pwn', 'data') FROM clusters` | SQLite | `Select` (with `Anonymous`) | **REJECTED** (In `DANGEROUS_FUNCTIONS`) | N/A | **PROTECTED** |
| 13 | `SELECT custom_unrecognized_fn(id) FROM clusters` | SQLite | `Select` (with `Anonymous`) | **REJECTED** ("not allowed by policy") | N/A | **PROTECTED** |
| 14 | `SELECT sqrt(node_count) FROM clusters` | SQLite | `Select` (with `Sqrt` / `Func`) | **ACCEPTED (is_valid=True)** | Permitted | **INCOMPLETE ALLOWLIST** |
| 15 | `SELECT generate_series(1, 10) FROM clusters` | PostgreSQL | `Select` (with `ExplodingGenerateSeries`) | **ACCEPTED (is_valid=True)** | Permitted | **INCOMPLETE ALLOWLIST** |
| 16 | `SELECT * FROM clusters WHERE id IN (SELECT cluster_id FROM nodes WHERE load_extension('x'))` | SQLite | `Select` (Nested `Anonymous`) | **REJECTED** (Recursive check) | N/A | **PROTECTED** |
| 17 | `SELECT * FROM clusters WHERE id IN (SELECT cluster_id FROM nodes FOR UPDATE)` | PostgreSQL | `Select` (Nested `Lock`) | **ACCEPTED (is_valid=True)** | Blocked in read-only transaction | **VULNERABLE (AST Bypass)** |
| 18 | `WITH cte AS (SELECT * FROM clusters FOR UPDATE) SELECT * FROM cte` | PostgreSQL | `Select` (CTE `Lock`) | **ACCEPTED (is_valid=True)** | Blocked in read-only transaction | **VULNERABLE (AST Bypass)** |
| 19 | `SELECT * FROM clusters UNION SELECT * FROM clusters FOR UPDATE` | PostgreSQL | `Union` (Branch `Lock`) | **ACCEPTED (is_valid=True)** | Blocked in read-only transaction | **VULNERABLE (AST Bypass)** |

### AST Bypass Findings Detail

1. **`exp.Into` Bypass**:
   When SQLGlot parses `SELECT ... INTO`, it attaches an `exp.Into` node to the `exp.Select` tree. Because `exp.Into` was not included in `DANGEROUS_NODE_TYPES`, the validator checks `tbl_node.name` against `OPERATIONAL_TABLES`. If the target table name matches an operational table (`SELECT * INTO clusters FROM nodes`), AST validation marks it valid!
2. **`exp.Lock` Bypass**:
   `SELECT ... FOR UPDATE` generates an `exp.Lock(update=True)` node. Because `exp.Lock` is missing from `DANGEROUS_NODE_TYPES`, row locking operations pass AST validation unimpeded across root queries, nested subqueries, CTEs, and UNION branches.
3. **Function Allowlist Inconsistency**:
   Lines 337–358 of `src/advanced_rag/text2sql/security.py`:
   - `exp.Anonymous` checks both `DANGEROUS_FUNCTIONS` and `ALLOWED_FUNCTIONS` (fails closed).
   - `exp.Func` (SQLGlot built-in function classes) only checks `DANGEROUS_FUNCTIONS`, completely skipping `ALLOWED_FUNCTIONS`. Built-in functions like `generate_series`, `sqrt`, `cbrt`, etc., bypass the allowlist.

---

## 6. PostgreSQL Role and Effective Permissions Audit

Inspection of `scripts/provision_postgres_readonly.sql` (57 lines):

### Static Role Analysis

1. **Role Definition**:
   ```sql
   CREATE ROLE text2sql_readonly WITH LOGIN PASSWORD 'change_me_in_production';
   ```
   - **Superuser**: Defaults to `NOSUPERUSER` in PostgreSQL (unless creator overrides).
   - **Database / Role Creation**: Defaults to `NOCREATEDB`, `NOCREATEROLE`.
   - **Inheritance**: Defaults to `INHERIT`. The script does not specify `NOINHERIT`. If `text2sql_readonly` is later granted an administrative role, it will inherit those permissions.
2. **Session Configuration**:
   ```sql
   ALTER ROLE text2sql_readonly SET default_transaction_read_only = ON;
   ALTER ROLE text2sql_readonly SET statement_timeout = '15s';
   ```
   Sets role-level configuration defaults. Note: In PostgreSQL, session-level settings can be changed within a session unless table/database permissions prevent unauthorized actions.
3. **Privilege Revocations & Grants**:
   ```sql
   REVOKE ALL ON DATABASE CURRENT_DATABASE() FROM text2sql_readonly;
   REVOKE CREATE ON SCHEMA public FROM text2sql_readonly;
   REVOKE ALL ON ALL TABLES IN SCHEMA public FROM text2sql_readonly;
   GRANT USAGE ON SCHEMA public TO text2sql_readonly;
   GRANT SELECT ON TABLE clusters, nodes, deployments, incidents, slo_breaches TO text2sql_readonly;
   REVOKE ALL ON TABLE document_metadata, ingestion_runs FROM text2sql_readonly;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM text2sql_readonly;
   ```
   - **Explicit Table Boundary**: Properly isolates the 5 operational tables and revokes access to `document_metadata` and `ingestion_runs`.
   - **PUBLIC Pseudorole Risk**: In standard PostgreSQL installations, permissions granted to `PUBLIC` are inherited by all roles. If `document_metadata` was created with default `PUBLIC` select access, revoking from `text2sql_readonly` alone does not revoke the inherited `PUBLIC` privilege. A hardening script should include `REVOKE ALL ON TABLE ... FROM PUBLIC;`.
   - **Sequences and Functions**: The script revokes table access, but does not revoke access to schema sequences (`REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM text2sql_readonly;`).
4. **Live Verification Separation**:
   *Notice*: No live PostgreSQL server is active in the local development environment. All findings in this section are based strictly on static code and SQL script review, not live PostgreSQL privilege queries.

---

## 7. SQLite Read-Only Audit & Connection Verification

The SQLite read-only implementation was verified using isolated temporary databases in `scratch/test_readonly_engine_behavior.py`:

### Verification Results

1. **URI Mode `mode=ro`**:
   `sql_readonly_url` constructs `sqlite+pysqlite:///file:.../test_ops.db?mode=ro&uri=true`.
   SQLite C-level file open flags enforce read-only access.
2. **Connection Pragma `PRAGMA query_only = ON;`**:
   The SQLAlchemy listener on the engine fires on every connection creation:
   ```python
   @event.listens_for(engine, "connect")
   def _set_sqlite_readonly(dbapi_connection, connection_record):
       cursor = dbapi_connection.cursor()
       cursor.execute("PRAGMA query_only = ON;")
       cursor.close()
   ```
3. **Database-Level Write Denial**:
   - `INSERT`: Failed with `OperationalError: (sqlite3.OperationalError) attempt to write a readonly database`.
   - `UPDATE`: Failed with `OperationalError: (sqlite3.OperationalError) attempt to write a readonly database`.
   - `DELETE`: Failed with `OperationalError: (sqlite3.OperationalError) attempt to write a readonly database`.
   - `CREATE TABLE`: Failed with `OperationalError: (sqlite3.OperationalError) attempt to write a readonly database`.
   - `DROP TABLE`: Failed with `OperationalError: (sqlite3.OperationalError) attempt to write a readonly database`.
4. **Connection Pool Persistence**:
   Subsequent connections checked out from the engine pool continued to reject mutations with `attempt to write a readonly database`.
5. **Application Write Engine Independence**:
   Tested alongside the application engine (`get_engine()`). Application sessions successfully performed inserts and updates against the database while the read-only engine simultaneously blocked all write operations.

---

## 8. Task Scope Audit & Carry-Forward Note for Task 60

### Already Implemented Execution-Limit Features (Tasks 58 & 59)
- **Row Limit Bounding**:
  - `_enforce_limit(query: str, row_limit: int)` in `src/advanced_rag/text2sql/security.py`: Uses regex to find `LIMIT \d+` and clamps values exceeding `settings.sql_row_limit` (default 200). Appends `LIMIT {row_limit}` if no limit is specified.
  - `executor.py` default limit parameter (`max_limit=100`).
- **PostgreSQL Statement Timeout**:
  - `ALTER ROLE text2sql_readonly SET statement_timeout = '15s';` in `scripts/provision_postgres_readonly.sql`.
  - Configuration setting `sql_timeout_seconds: int = 15` in `src/advanced_rag/config.py`.

### Carry-Forward Scope for Task 60 (Timeouts and Cancellation)
The following capabilities remain unimplemeted and must be built in Task 60:
1. **SQLite Query Timeout**:
   SQLite does not support `statement_timeout`. Task 60 must implement `sqlite3.set_progress_handler` or connection-level interrupt timers to abort long-running SQLite queries.
2. **Python / Async Timeout Wrapper**:
   `executor.execute()` currently executes synchronously via SQLAlchemy `conn.execute()` without a Python-level `asyncio.timeout()` or execution cancellation token.
3. **Dynamic Per-Query Timeout Support**:
   Support for caller-specified execution deadlines (`execute(sql, timeout_seconds=...)`).
4. **Client Disconnect Cancellation**:
   Graceful query termination when FastAPI HTTP requests are aborted.

---

## 9. Inventory of Inspected Files, Functions, and Scripts

### Source Files Inspected
- [`src/advanced_rag/config.py`](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/config.py): Lines 120–125, 199–228 (`postgres_dsn`, `postgres_readonly_dsn`, `sql_url`, `sql_readonly_url`, `sql_dialect`).
- [`src/advanced_rag/text2sql/database.py`](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/text2sql/database.py): Lines 43–107 (DDL), 115–183 (notes), 210–260 (`get_engine`, `get_readonly_engine`, event listeners).
- [`src/advanced_rag/text2sql/security.py`](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/text2sql/security.py): Lines 23–187 (allowlists, dangerous nodes/functions), 224–532 (`validate_sql_ast`, AST traversal).
- [`src/advanced_rag/text2sql/executor.py`](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/text2sql/executor.py): Lines 25–100 (`validate`, `execute`, `normalize`).
- [`src/advanced_rag/text2sql/schema.py`](file:///C:/Users/ASUS/advanced-rag/src/advanced_rag/text2sql/schema.py): Lines 29–74 (`OPERATIONAL_TABLES`, `SAMPLE_QUESTIONS`), 77–150 (`get_schema_prompt`).
- [`scripts/provision_postgres_readonly.sql`](file:///C:/Users/ASUS/advanced-rag/scripts/provision_postgres_readonly.sql): Lines 1–57.
- [`docs/STAGE_5_TASK_57_INDEPENDENT_AUDIT.md`](file:///C:/Users/ASUS/advanced-rag/docs/STAGE_5_TASK_57_INDEPENDENT_AUDIT.md): Sections B, C, D, E.
- [`docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md`](file:///C:/Users/ASUS/advanced-rag/docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md): Sections 1–16.

### Diagnostic Scripts Executed
- `scratch/inspect_db_schema.py`: Read-only inspection of physical SQLite tables and columns.
- `scratch/test_ast_security_bypass.py`: 19 diagnostic AST parse & validation tests.
- `scratch/test_readonly_engine_behavior.py`: Multi-connection SQLite read-only isolation test.
- `scratch/test_dsn_fallback.py`: DSN fallback verification.

---

## 10. Recommended Corrections (For Subsequent Implementation)

*Note: In accordance with audit instructions, no files have been modified. The following recommendations must be implemented in a dedicated remediation turn.*

### 1. Fix PostgreSQL Fail-Closed Enforcement (`src/advanced_rag/config.py`)
Modify `sql_readonly_url` to raise a `ValueError` or fail closed when `postgres_dsn` is configured but `postgres_readonly_dsn` is missing:
```python
@property
def sql_readonly_url(self) -> str:
    """SQLAlchemy URL for Text2SQL read-only execution."""
    if self.postgres_readonly_dsn:
        dsn = self.postgres_readonly_dsn
        if dsn.startswith("postgresql://"):
            return "postgresql+psycopg://" + dsn[len("postgresql://"):]
        return dsn
    if self.postgres_dsn:
        raise ValueError(
            "POSTGRES_READONLY_DSN must be explicitly configured when PostgreSQL is used. "
            "Falling back to application write credentials for Text2SQL is strictly forbidden."
        )
    path = self.absolute(self.sqlite_path)
    return f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"
```

### 2. Add Missing Dangerous AST Node Types (`src/advanced_rag/text2sql/security.py`)
Add `exp.Into` and `exp.Lock` to `DANGEROUS_NODE_TYPES`:
```python
DANGEROUS_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Pragma,
    exp.Command,
    exp.Attach,
    exp.Detach,
    exp.TruncateTable,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,   # <--- Prevent SELECT ... INTO
    exp.Lock,   # <--- Prevent SELECT ... FOR UPDATE / FOR SHARE
)
```

### 3. Enforce Closed Function Allowlist for `exp.Func` (`src/advanced_rag/text2sql/security.py`)
Ensure `exp.Func` instances also validate against `ALLOWED_FUNCTIONS`:
```python
elif isinstance(node, exp.Func):
    fn_name = (node.sql_name() or type(node).__name__).lower()
    if fn_name in DANGEROUS_FUNCTIONS:
        return SqlValidationResult(is_valid=False, error=f"Forbidden function '{fn_name}' is not permitted.")
    if fn_name not in ALLOWED_FUNCTIONS:
        return SqlValidationResult(is_valid=False, error=f"Function '{fn_name}' is not allowed by policy.")
```

### 4. Harden PostgreSQL Provisioning Script (`scripts/provision_postgres_readonly.sql`)
1. Add `NOINHERIT` to `CREATE ROLE text2sql_readonly WITH LOGIN NOINHERIT PASSWORD ...`.
2. Add explicit revocations on `PUBLIC`:
   ```sql
   REVOKE ALL ON TABLE public.document_metadata FROM PUBLIC;
   REVOKE ALL ON TABLE public.ingestion_runs FROM PUBLIC;
   REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM text2sql_readonly;
   ```

### 5. Correct `docs/STAGE_5_TASKS_58_59_IMPLEMENTATION_REPORT.md`
Update Section 6 to replace the hallucinated schema with the true physical schema matching `data/ops.db` and `security.py`.
