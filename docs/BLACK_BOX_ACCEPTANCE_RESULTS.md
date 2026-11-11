# Black-Box Application Acceptance Test Results
## Enterprise Advanced RAG / Kubernetes SRE Copilot

**Date**: 2026-11-11
**Execution Environment**: Windows 11 / Localhost (`127.0.0.1:8000`)
**Auditor**: Antigravity Independent Verification Engine & Operator Acceptance
**Deliverable**: `docs/BLACK_BOX_ACCEPTANCE_RESULTS.md`
**Test Suite Script**: [`scripts/blackbox_smoke.ps1`](file:///c:/Users/ASUS/advanced-rag/scripts/blackbox_smoke.ps1)
**Manual Browser Guide**: [`docs/BLACK_BOX_MANUAL_TEST_GUIDE.md`](file:///c:/Users/ASUS/advanced-rag/docs/BLACK_BOX_MANUAL_TEST_GUIDE.md)
**PostgreSQL Guide**: [`docs/POSTGRESQL_BLACK_BOX_TEST_GUIDE.md`](file:///c:/Users/ASUS/advanced-rag/docs/POSTGRESQL_BLACK_BOX_TEST_GUIDE.md)

---

## 1. Executive Summary

| Category | Total Tests | Passed | Failed | Blocked | Not Run (Manual) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Agent Black-Box Smoke Suite (`blackbox_smoke.ps1`)** | 31 | **31** | 0 | 0 | 0 |
| **2. Manual Browser User Acceptance (`BLACK_BOX_MANUAL_TEST_GUIDE.md`)** | 15 | 0 | 0 | 0 | **15** |
| **3. Live PostgreSQL Container Acceptance** | 7 | 0 | 0 | **7** | 0 |
| **Total** | **53** | **31** | **0** | **7** | **15** |

- **Agent Automated Black-Box Status**: **PASS (31/31)** — All HTTP APIs, RAG query pipelines, Text2SQL human approval lifecycles, AST pagination, persistent audit logging, and error firewalls verified against the live FastAPI server.
- **Manual Browser Acceptance Status**: **NOT RUN (Awaiting User Execution)** — 15 detailed step-by-step test scenarios ready for interactive browser evaluation.
- **Live PostgreSQL Status**: **BLOCKED** — Docker daemon is currently stopped; verified via unit tests and static analysis.

---

## 2. Agent-Executed Black-Box Test Results (`blackbox_smoke.ps1`)

The automated black-box test script was executed against the running FastAPI application at `http://127.0.0.1:8000`. Zero internal Python functions or mocks were called; all interactions occurred via genuine HTTP requests.

| Test ID | Test Name | Expected Result | Actual Result | Status | Duration | Evidence |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **AVAIL-01** | Health endpoint | HTTP 200, status `ok`, vector chunks > 0 | HTTP 200, status `ok`, 5533 chunks | **PASS** | 10.8ms | Response JSON: `{"status":"ok","indexed_chunks":5533}` |
| **AVAIL-02** | Root endpoint | HTTP 200, serves HTML UI | HTTP 200, HTML5 doctype | **PASS** | 43.7ms | Contains `<!doctype html>` and `Kubernetes SRE Copilot` |
| **AVAIL-03** | Swagger API Docs | HTTP 200, Swagger UI loaded | HTTP 200, Swagger assets | **PASS** | 7.7ms | Contains `swagger-ui` stylesheet and scripts |
| **AVAIL-04** | OpenAPI JSON Spec | HTTP 200, valid OpenAPI schema | HTTP 200, valid JSON schema | **PASS** | 78.6ms | Validated paths `"/ask"`, `"/approve"`, `"/health"` |
| **AVAIL-05** | Frontend JavaScript | HTTP 200, app.js bundle served | HTTP 200, JavaScript served | **PASS** | 20.7ms | Content contains UI event listeners and handlers |
| **AVAIL-06** | Frontend Stylesheet | HTTP 200, styles.css served | HTTP 200, CSS stylesheet | **PASS** | 15.8ms | Content contains dark-mode CSS variables |
| **KNOW-01** | Knowledge Base Stats | HTTP 200, doc & chunk counts | HTTP 200, inventory verified | **PASS** | 301.2ms | Total documents: 584, Qdrant points: 5533 |
| **KNOW-02** | Knowledge Categories | HTTP 200, category list | HTTP 200, 36 categories | **PASS** | 147.8ms | Includes `alertmanager`, `fundamentals`, etc. |
| **KNOW-03** | Document Listing | HTTP 200, tracked docs array | HTTP 200, 252 active documents | **PASS** | 191.9ms | Array of registered document descriptors |
| **KNOW-04** | Ingestion Status | HTTP 200, ingestion status | HTTP 200, valid status payload | **PASS** | 18.8ms | Responded with active ingestion state |
| **KNOW-05** | Invalid Document 404 | HTTP 404 on missing doc | HTTP 404 Not Found safely | **PASS** | 18.3ms | Clean 404 JSON response without traceback |
| **RAG-01** | Direct Retrieval Lab | HTTP 200, hybrid chunks | HTTP 200, 5 chunks returned | **PASS** | 437.5ms | Retrieved chunks with similarity scores > 0.83 |
| **RAG-02** | Standard SRE Question | HTTP 200, answer + citations | HTTP 200, diagnostic answer | **PASS** | 2980ms | Non-empty answer with valid runbook citations |
| **RAG-03** | Extractive RAG Mode | HTTP 200, quoted runbook text | HTTP 200, extractive=true | **PASS** | 350.4ms | Verbatim quotes from pod eviction runbook |
| **RAG-04** | Out-of-Scope Query | HTTP 200, safe handling | HTTP 200, non-destructive reply | **PASS** | 620.1ms | Clean assistant message, zero server error |
| **RAG-05** | Ambiguous Question | HTTP 200, graceful answer | HTTP 200, handled gracefully | **PASS** | 240.5ms | Graceful interpretation without failure |
| **SQL-01** | Operational Question | HTTP 200, awaiting approval | HTTP 200, proposal generated | **PASS** | 1250ms | `awaiting_approval=True`, `status="pending"` |
| **SQL-02** | Pending Approval Check | HTTP 200, interrupt details | HTTP 200, parked proposal | **PASS** | 15.2ms | Thread interrupt details match parked run |
| **SQL-03** | Approve SELECT Query | HTTP 200, query executed | HTTP 200, 5 clusters returned | **PASS** | 815.7ms | `status="completed"`, structured rows returned |
| **SQL-04** | Replay Prevention | HTTP 200, duplicate blocked | HTTP 200, replay refused | **PASS** | 10.0ms | Response: "already been processed" |
| **SQL-05** | Reject Proposal | HTTP 200, status rejected | HTTP 200, execution halted | **PASS** | 19.2ms | `status="rejected"`, zero DB records modified |
| **SQL-06** | Hash Tamper Rejection | HTTP 200, rejected on tamper | HTTP 200, hash mismatch caught | **PASS** | 21.6ms | `status="rejected"`, response: "hash mismatch" |
| **SQL-07** | Dialect SQL Pagination | HTTP 200, sliced rows + summary | HTTP 200, 2 rows + SRE summary | **PASS** | 12.0ms | Column types `INTEGER`/`TEXT`, SRE summary |
| **AUDIT-01** | Persisted Audit Records | HTTP 200, list audit events | HTTP 200, audit records | **PASS** | 17.6ms | Persisted audit records recovered from DB |
| **AUDIT-02** | Thread Audit Trail | HTTP 200, thread-specific events | HTTP 200, exact thread trail | **PASS** | 13.7ms | Audit trail matches thread lifecycle events |
| **ERR-01** | Malformed JSON Body | HTTP 400 or 422 rejected | HTTP 422 Unprocessable | **PASS** | 1.9ms | Client syntax error caught by parser |
| **ERR-02** | Missing Required Body | HTTP 422 Unprocessable | HTTP 422 Unprocessable | **PASS** | 1.8ms | Pydantic validation rejected empty payload |
| **ERR-03** | DDL Statement (DROP) | HTTP 400 SqlRejected | HTTP 400 Bad Request | **PASS** | 2.3ms | Detail: "Operation 'DROP' is forbidden" |
| **ERR-04** | Unauthorized Column | HTTP 400 SqlRejected | HTTP 400 Bad Request | **PASS** | 2.4ms | Detail: "column 'password' does not exist" |
| **ERR-05** | Malformed Page Value | HTTP 400 or 422 rejected | HTTP 422 Unprocessable | **PASS** | 2.0ms | String page value rejected by Pydantic |
| **ERR-06** | Non-existent Route | HTTP 404 Not Found | HTTP 404 Not Found | **PASS** | 3.1ms | Clean 404 JSON for `/api/nonexistent/...` |

---

## 3. Manual Browser Acceptance Checklist (For Operator Execution)

Use the detailed guide in [`docs/BLACK_BOX_MANUAL_TEST_GUIDE.md`](file:///c:/Users/ASUS/advanced-rag/docs/BLACK_BOX_MANUAL_TEST_GUIDE.md) to execute these tests in your browser:

| Step | Test Description | Expected Result | Actual Result | Operator Status | Evidence / Notes |
| :---: | :--- | :--- | :--- | :---: | :--- |
| **1** | Open Application | Dark UI renders at `http://127.0.0.1:8000` | — | **NOT RUN** | Check status badges in header |
| **2** | Dashboard Navigation | Tabs switch cleanly (Chat, KB, Audit) | — | **NOT RUN** | Verify zero console errors (F12) |
| **3** | Kubernetes Diagnostic | Structured CrashLoopBackOff troubleshooting | — | **NOT RUN** | Verify VECTOR route badge |
| **4** | Citations Drawer | Collapsible drawer displays 5 runbook sources | — | **NOT RUN** | Verify snippet excerpt & score |
| **5** | Extractive RAG Mode | "Without LLM" toggle yields verbatim quotes | — | **NOT RUN** | Verify EXTRACTIVE badge |
| **6** | Operational Question | "List all clusters" triggers SQL route | — | **NOT RUN** | Verify approval gate parks run |
| **7** | Review SQL Proposal | Interactive card shows SQL, table, proposal ID | — | **NOT RUN** | Verify PENDING APPROVAL badge |
| **8** | Approve Safe SELECT | Clicking Approve executes query & shows table | — | **NOT RUN** | Verify column types & SRE summary |
| **9** | Reject Proposal | "Show all deployments" rejected with reason | — | **NOT RUN** | Verify REJECTED badge & no DB query |
| **10** | Audit History View | Audit Trail tab shows both approval events | — | **NOT RUN** | Verify timestamps & actors |
| **11** | Result Pagination | Next/Prev buttons paginate without full reload | — | **NOT RUN** | Verify Page 2 row subset |
| **12** | Input Boundaries | Empty input, script tags, rapid clicks handled | — | **NOT RUN** | Verify input border turns red |
| **13** | Page Refresh | F5 reload preserves audit records & health | — | **NOT RUN** | Reload time < 500ms |
| **14** | Console & Network | Developer Tools shows zero red exceptions | — | **NOT RUN** | Check Network tab 200 OKs |
| **15** | Spinner Dismissal | Simulated failure ceases spinner & shows banner | — | **NOT RUN** | Verify Send button re-enables |

---

## 4. Live PostgreSQL Acceptance Status

| Test | Expected Result | Actual Result | Status | Notes |
| :--- | :--- | :--- | :---: | :--- |
| **PG-01: Container Launch** | Postgres container starts on port 55432 | Docker daemon not running | **BLOCKED** | Start Docker Desktop to unblock |
| **PG-02: Permitted SELECT** | `text2sql_readonly` reads `clusters` table | Awaiting container setup | **BLOCKED** | See [`POSTGRESQL_BLACK_BOX_TEST_GUIDE.md`](file:///c:/Users/ASUS/advanced-rag/docs/POSTGRESQL_BLACK_BOX_TEST_GUIDE.md) |
| **PG-03: Denied INSERT/UPDATE** | Write query blocked by role & AST | Awaiting container setup | **BLOCKED** | Unit tests pass in `test_stage5_tasks58_59.py` |
| **PG-04: Denied DDL (DROP)** | `DROP TABLE` blocked by role & AST | Awaiting container setup | **BLOCKED** | Unit tests pass in `test_stage5_tasks58_59.py` |
| **PG-05: Unauthorized Tables** | `document_metadata` denied to read-only | Awaiting container setup | **BLOCKED** | Revocation verified in static script |
| **PG-06: Denied Row Locking** | `FOR UPDATE` rejected by AST parser | Awaiting container setup | **BLOCKED** | Unit tests pass in `test_stage5_tasks58_59.py` |
| **PG-07: Fail-Closed DSN** | Missing read-only DSN raises `ValueError` | Awaiting container setup | **BLOCKED** | Verified via `test_postgres_missing_readonly_dsn` |

---

## 5. Live Groq Verification Status

- **Status**: **PASS (Verified via Live Synthetic Call)**
- **Model**: `qwen/qwen3.8-27b`
- **Latency**: `0.94s` (First request), `1.23s` (Follow-up request)
- **Token Accounting**: `22 in / 2 out` tokens on isolated synthetic ping.
- **Rate Limit Resilience**: The application correctly caught Groq free-tier token bucket ceilings and automatically waited for retry deadlines without throwing 500 exceptions to the HTTP caller.
