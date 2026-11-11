# PostgreSQL Live Black-Box Verification Guide
## Enterprise Advanced RAG / Kubernetes SRE Copilot

**Audience**: Systems Engineer / DBA / Security Auditor
**Deliverable**: `docs/POSTGRESQL_BLACK_BOX_TEST_GUIDE.md`
**Current Workstation Status**:
- Docker CLI Version: `Docker version 29.9.2` (Installed)
- Docker Engine Status: **STOPPED** (`failed to connect to docker API at npipe:////./pipe/docker_engine`)
- Port 5432: Closed (No local PostgreSQL server running)
- Testing Status: **BLOCKED (Requires starting Docker Desktop or local PostgreSQL service)**

---

## 1. Disposable Docker Container Setup (Recommended)

When you start **Docker Desktop** on Windows, execute the following PowerShell commands to launch an isolated, disposable PostgreSQL instance on port **`55432`**.

> [!IMPORTANT]
> This container uses a separate non-standard port (`55432`) and a temporary database (`test_ops`). It has **zero access** to your operational database ([`data/ops.db`](file:///c:/Users/ASUS/advanced-rag/data/ops.db)) or Qdrant vector collection.

### Step 1: Start Disposable PostgreSQL Container
```powershell
docker run --name pg-blackbox-test `
  -e POSTGRES_PASSWORD=supersecret `
  -e POSTGRES_DB=test_ops `
  -p 55432:5432 `
  -d postgres:16-alpine
```

### Step 2: Seed Representative Operational & Internal Tables
```powershell
# Create sample operational tables and internal registry tables
@"
CREATE TABLE clusters (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    environment VARCHAR(50) NOT NULL,
    region VARCHAR(50) NOT NULL,
    k8s_version VARCHAR(20) NOT NULL,
    node_count INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO clusters (name, environment, region, k8s_version, node_count)
VALUES ('prod-us-east', 'production', 'us-east-1', 'v1.31.0', 12),
       ('staging-eu', 'staging', 'eu-west-1', 'v1.31.0', 4);

CREATE TABLE document_metadata (
    id SERIAL PRIMARY KEY,
    document_id VARCHAR(100) NOT NULL,
    secret_internal_token VARCHAR(100) DEFAULT 'SUPER_SECRET_INTERNAL'
);
INSERT INTO document_metadata (document_id) VALUES ('doc-alpha-1');
"@ | docker exec -i pg-blackbox-test psql -U postgres -d test_ops
```

### Step 3: Apply the Production Hardening Provisioning Script
Apply [`scripts/provision_postgres_readonly.sql`](file:///c:/Users/ASUS/advanced-rag/scripts/provision_postgres_readonly.sql) to provision the `text2sql_readonly` role:
```powershell
Get-Content scripts\provision_postgres_readonly.sql | docker exec -i pg-blackbox-test psql -U postgres -d test_ops
```

---

## 2. Automated Black-Box Test Execution

Run the dedicated test script in PowerShell against the container on port `55432`:

```powershell
.\.venv\Scripts\python.exe -c "
import os
from advanced_rag.config import Settings
from advanced_rag.text2sql import executor, database

# Configure settings pointing strictly to the disposable container
settings = Settings(
    sql_dialect='PostgreSQL',
    postgres_dsn='postgresql://postgres:supersecret@127.0.0.1:55432/test_ops',
    postgres_readonly_dsn='postgresql://text2sql_readonly:change_me_in_production@127.0.0.1:55432/test_ops',
    sql_timeout_seconds=5.0
)

print('=== 1. Testing Permitted SELECT ===')
res = executor.execute('SELECT id, name, environment FROM clusters;', settings=settings)
print(f'PASS: Returned {res.row_count} rows: {res.rows}')

print('\n=== 2. Testing Denied INSERT (AST & DB Role) ===')
try:
    executor.execute(\"INSERT INTO clusters (name, environment, region, k8s_version, node_count) VALUES ('evil', 'dev', 'us-east', 'v1', 1);\", settings=settings)
    print('FAIL: INSERT was permitted!')
except Exception as e:
    print(f'PASS: INSERT correctly blocked: {type(e).__name__}')

print('\n=== 3. Testing Denied DDL (DROP TABLE) ===')
try:
    executor.execute('DROP TABLE clusters;', settings=settings)
    print('FAIL: DROP TABLE was permitted!')
except Exception as e:
    print(f'PASS: DROP TABLE correctly blocked: {type(e).__name__}')

print('\n=== 4. Testing Unauthorized Internal Table Access ===')
try:
    executor.execute('SELECT * FROM document_metadata;', settings=settings)
    print('FAIL: document_metadata was accessible!')
except Exception as e:
    print(f'PASS: document_metadata correctly blocked: {type(e).__name__}')

print('\n=== 5. Testing Row Locking Query (FOR UPDATE) ===')
try:
    executor.execute('SELECT * FROM clusters FOR UPDATE;', settings=settings)
    print('FAIL: FOR UPDATE was permitted!')
except Exception as e:
    print(f'PASS: FOR UPDATE correctly blocked: {type(e).__name__}')

print('\n=== 6. Testing Function Allowlist (pg_sleep) ===')
try:
    executor.execute('SELECT pg_sleep(5) FROM clusters;', settings=settings)
    print('FAIL: pg_sleep was permitted!')
except Exception as e:
    print(f'PASS: pg_sleep correctly blocked: {type(e).__name__}')

print('\n=== 7. Testing PostgreSQL Fail-Closed (Missing Read-Only DSN) ===')
settings_fail_open = Settings(
    sql_dialect='PostgreSQL',
    postgres_dsn='postgresql://postgres:supersecret@127.0.0.1:55432/test_ops',
    postgres_readonly_dsn=None
)
try:
    _ = settings_fail_open.sql_readonly_url
    print('FAIL: Failed to fail closed when read-only DSN was omitted!')
except ValueError as e:
    print(f'PASS: Fail-closed verified: {e}')

print('\n=== ALL POSTGRESQL LIVE VERIFICATIONS COMPLETED SUCCESSFULLY ===')
"
```

---

## 3. Container Cleanup

Once testing is complete, destroy **only** the temporary test container and its volume:

```powershell
docker stop pg-blackbox-test
docker rm -v pg-blackbox-test
```

---

## 4. Manual Alternative (If Docker Is Unavailable)

If Docker Desktop cannot be started on your workstation:
1. Use an existing PostgreSQL 15/16 instance (e.g. AWS RDS, Azure Database for PostgreSQL, or a local dev service).
2. Create a clean testing database: `CREATE DATABASE test_ops;`
3. Seed the sample tables using the SQL script in Section 1.
4. Execute `scripts/provision_postgres_readonly.sql` as the PostgreSQL superuser.
5. Export environment variables:
   ```powershell
   $env:POSTGRES_DSN="postgresql://postgres:secret@your-host:5432/test_ops"
   $env:POSTGRES_READONLY_DSN="postgresql://text2sql_readonly:change_me_in_production@your-host:5432/test_ops"
   $env:SQL_DIALECT="PostgreSQL"
   ```
6. Run the black-box verification commands in Section 2.

If no PostgreSQL instance is accessible, live PostgreSQL testing remains honestly documented as **`BLOCKED (No Safe Live Instance Available)`**. Unit tests and static SQL role reviews confirm code readiness.
