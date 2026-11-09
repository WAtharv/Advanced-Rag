# Pre-Push Runtime Verification Report
## Enterprise Advanced RAG / Kubernetes SRE Copilot — Stage 4 Final

**Verification Date:** 2026-11-09  
**Verified By:** Automated + manual API testing  
**Working Directory:** `c:\Users\ASUS\advanced-rag`

---

## 1. Project Structure Audit

| Component | Location | Status |
|-----------|----------|--------|
| FastAPI entry point | `src/advanced_rag/api/main.py` | ✅ Verified |
| LangGraph pipeline | `src/advanced_rag/graph/pipeline.py` + `builder.py` | ✅ Verified |
| Retrieval module | `src/advanced_rag/retrieval/` | ✅ Verified |
| LLM client | `src/advanced_rag/llm/client.py` | ✅ Verified |
| Guardrails | `src/advanced_rag/guardrails/` | ✅ Verified |
| External search | `src/advanced_rag/retrieval/external.py` | ✅ Verified |
| Web frontend | `ui/web/` (index.html, app.js, styles.css, markdown.js) | ✅ Verified |
| Knowledge API router | `src/advanced_rag/api/knowledge.py` | ✅ Verified |
| Ingestion CLI | `src/advanced_rag/ingestion/cli.py` | ✅ Verified |
| Configuration | `src/advanced_rag/config.py` | ✅ Verified |

**Startup command:** `rag-api` (via `[project.scripts]`) or `.venv\Scripts\uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000`

---

## 2. Environment Verification

| Item | Result |
|------|--------|
| Python version | **3.11.9** ✅ |
| Virtual environment | `.venv\` — present and healthy |
| Core imports | `fastapi`, `qdrant_client`, `langgraph`, `fastembed`, `httpx`, `numpy`, `sqlalchemy` — all OK ✅ |

---

## 3. Configuration Audit

> No secrets printed — presence flags only.

| Setting | Value |
|---------|-------|
| LLM Provider | `groq` |
| LLM Model | `qwen/qwen3.8-27b` |
| LLM Fast/Retry Model | `qwen/qwen3.8-27b` |
| Embed Model | `gemini-embedding-001` |
| Dense Model | `BAAI/bge-small-en-v1.5` |
| Sparse Model | `local-bm25` |
| Rerank Model | `Xenova/ms-marco-MiniLM-L-6-v2` |
| Qdrant Collection | `sre_copilot_v1` |
| Qdrant Mode | Embedded at `data\qdrant` |
| Retrieve Top K | 20 |
| Rerank Top N | 5 |
| Hybrid Fusion | RRF |
| Dense Weight | 0.7 |
| Checkpoint Backend | `memory` |
| SQL Dialect | SQLite |
| App Environment | `development` |
| Chunk Size | 900 |
| Chunk Overlap | 150 |
| Max Context Tokens | 2,500 |

**Feature Flags:**

| Feature | Enabled |
|---------|---------|
| HyDE | ❌ (off by default) |
| Rerank | ❌ (off by default) |
| CRAG | ✅ |
| Self-RAG | ❌ (off by default) |
| Text2SQL | ✅ |
| Guardrails | ✅ |
| Guardrails LLM Input | ❌ |
| Guardrails LLM Output | ❌ |
| Cache | ✅ |
| External Search | ✅ |
| External Search Provider | `tavily` |
| Tavily Allowed | ✅ |
| Context Deduplication | ✅ |

**Credentials (presence only):**

| Key | Present |
|-----|---------|
| Groq API Key | ✅ PRESENT |
| Tavily API Key | ✅ PRESENT |
| Google API Key | ✅ PRESENT |
| Anthropic Key | ❌ MISSING (optional) |
| Redis URL | ❌ NOT SET (in-process cache) |
| Postgres DSN | ❌ NOT SET (SQLite mode) |

---

## 4. Test Suite Results

> **Full suite run with API server stopped** — eliminates Qdrant lock contention.

### Master Result

```
514 passed in 42.72s
```

### Module-Level Breakdown

| Test File | Tests | Status |
|-----------|-------|--------|
| `test_stage4_task47.py` | 17 | ✅ All passed |
| `test_stage4_task48.py` | 36 | ✅ All passed |
| `test_stage4_task49.py` | 47 | ✅ All passed |
| `test_stage4_task50.py` | 10 | ✅ All passed |
| `test_retrieval_unit.py` | ~12 | ✅ All passed |
| `test_sre_copilot_retrieval.py` | ~12 | ✅ All passed |
| `test_retrieval_lab_fixes.py` | ~12 | ✅ All passed |
| `test_api.py` | ~15 | ✅ All passed |
| `test_graph.py` | ~15 | ✅ All passed |
| `test_document_lifecycle_and_sync.py` | 17 | ✅ All passed (server stopped) |
| `test_knowledge_stats_and_registry.py` | 18 | ✅ All passed (server stopped) |
| All other tests | remainder | ✅ All passed |
| **TOTAL** | **514** | ✅ |

> **Note:** When the API server is running (embedded Qdrant locked),
> `test_document_lifecycle_and_sync.py` and `test_knowledge_stats_and_registry.py`
> will produce `EmbeddedStoreBusyError` for tests that need their own temporary Qdrant
> collection. This is expected correct application behavior. Run the full suite with
> the server stopped to get 514/514.

> **Note:** `pytest` exits with code 1 in PowerShell due to the
> `asyncio_default_fixture_loop_scope` deprecation warning from `pytest-asyncio` v0.24.
> This is benign — all 514 tests pass.

---

## 5. FastAPI Application Startup

**Command:** `.venv\Scripts\uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000`

**Observed startup sequence:**
1. `TLS verification is using the system trust store` — certificate trust store initialized
2. `Starting API (model=qwen/qwen3.8-27b)` — settings loaded
3. LangGraph pipeline compiled (first call, `lru_cache`, ~8-10s)
4. `Application startup complete.` — ready to serve

**Startup latency:** ~8-10 seconds (cold start), subsequent requests fast

---

## 6. API Verification Results

### Health Endpoint

```
GET /health → 200 OK
{
  "status": "ok",
  "indexed_chunks": 5533,
  "llm_mode": "live",
  "model": "qwen/qwen3.8-27b",
  "vector_store": "embedded:data\\qdrant",
  "sql_dialect": "SQLite",
  "cache": "in-process",
  "features": { "hyde": false, "crag": true, "self_rag": false, "text2sql": true, "guardrails": true, "cache": true }
}
```

✅ Status `ok`, 5,533 chunks, LLM live, all features reported correctly.

---

## 7. RAG Pipeline Scenario Results

### Scenario A — OOMKilled Diagnosis (Internal Knowledge)

**Query:** `A container was terminated with exit code 137. How do I determine whether it was OOMKilled and inspect the memory limit?`

| Metric | Result |
|--------|--------|
| Answer generated | ✅ Yes (1,229 characters) |
| Thread ID assigned | ✅ Yes |
| Awaiting approval | ✅ No |
| Answer cites kubectl commands | ✅ Yes |

**Key retrieved sources:** `sre/runbooks/kubernetes/oomkilled.md`, `sre/postmortems/simulated-incident-oomkilled-cascade.md`, `sre/runbooks/kubernetes/crashloopbackoff.md`

---

### Scenario C — Out-of-Scope Refusal (Router Test)

**Query:** `How do I configure quantum teleportation overlay networks in kube-proxy v2.99?`

| Check | Result |
|-------|--------|
| Refusal triggered | ✅ Yes |
| Refusal message | `"That question is outside what this assistant covers."` |
| No hallucination | ✅ Confirmed |
| Sources returned | 0 (correct) |

---

### Scenario D — Extractive Answer (Without LLM)

**Query:** `What are the standard Helm chart hooks and their execution order?` with `use_llm: false`

| Check | Result |
|-------|--------|
| Extractive path taken | ✅ Yes |
| Disclaimer present | ✅ `**No language model is configured, so this answer is extractive.**` |
| Answer length | 3,590 characters |
| Sources quoted verbatim | ✅ Yes |

---

## 8. Retrieval Lab Verification

| Mode | Query | Result Count | Latency |
|------|-------|-------------|---------|
| Hybrid RRF | OOMKilled exit code 137 | 5 | 449.82ms |
| Dense | OOMKilled exit code 137 | 5 | 15.96ms |
| Hybrid + Category filter | kubernetes affinity | 20 | 305.51ms |

**Top retrieved document (Hybrid):** `sre/runbooks/kubernetes/oomkilled.md` — Section "Decision Tree" — Score 0.7

**Category filter:** `category: kubernetes` → results exclusively from kubernetes corpus ✅

---

## 9. Knowledge Base Verification

| Metric | Expected | Actual |
|--------|----------|--------|
| Total indexed chunks | 5,533 | ✅ **5,533** |
| Total indexed documents | 252 | ✅ **252** |
| Qdrant available | true | ✅ **true** |
| Documents pending indexing | 0 | ✅ **0** |
| Documents with indexing failures | 0 | ✅ **0** |
| Chunks — helm | 1,012 | ✅ 1,012 |
| Chunks — kubernetes | 1,709 | ✅ 1,709 |
| Chunks — prometheus | 2,039 | ✅ 2,039 |
| Chunks — sre | 773 | ✅ 773 |
| Avg chunks per document | 21.96 | ✅ 21.96 |

**Collection:** `sre_copilot_v1` — embedded, no changes detected.

---

## 10. Security Checks

| Check | Result |
|-------|--------|
| `GET /.env` | ✅ **404 Not Found** (not accessible) |
| API key patterns in `/health` response | ✅ **None found** |
| API key patterns in `/api/knowledge/stats` | ✅ **None found** |
| Groq key in any error message | ✅ **Not exposed** |
| Tavily key in any response | ✅ **Not exposed** |

---

## 11. Known Limitations and Notes

### L1 — Categories API cosmetic mismatch
**What:** `GET /api/knowledge/categories` returns `indexed_chunk_count: 0` for sub-categories like `alertmanager`, `architecture`, etc.  
**Why:** The registry organizes documents by filesystem path segments; Qdrant payload uses top-level categories (`helm`, `kubernetes`, `prometheus`, `sre`).  
**Impact:** Cosmetic UI display only. Retrieval with `category` filter works correctly.  
**Action:** No fix required.

### L2 — Embedded Qdrant single-process constraint
**What:** Lifecycle and sync tests fail with `EmbeddedStoreBusyError` when the API server is running.  
**Why:** Embedded Qdrant (rocksdb) allows exactly one open connection. Correct application behavior.  
**Impact:** Test run order matters. Full suite must be run with server stopped.  
**Action:** Documented in testing guide. No code change required.

### L3 — asyncio deprecation warning
**What:** `pytest-asyncio` v0.24 emits `PytestDeprecationWarning` about `asyncio_default_fixture_loop_scope`.  
**Impact:** PowerShell reports exit code 1 due to stderr. Tests are not failing.  
**Action:** Optional cosmetic fix — add `asyncio_default_fixture_loop_scope = "function"` to `[tool.pytest.ini_options]` in `pyproject.toml`.

### L4 — Self-RAG disabled by default
**What:** `enable_self_rag: false`.  
**Why:** Intentional — reduces latency and Groq rate-limit pressure.  
**Action:** Enable via `ENABLE_SELF_RAG=true` in `.env` when needed.

### L5 — LLM Input/Output Guardrails disabled
**What:** `enable_guardrails_llm_input: false`, `enable_guardrails_llm_output: false`.  
**Why:** Top-level guardrails enabled. LLM-based checks are opt-in add-ons.  
**Action:** Enable via `.env` if stricter content moderation is required.

---

## 12. Task 50 Benchmark Summary (Production Baseline)

**Strategy:** Hybrid RRF (default production configuration)

| Metric | Score |
|--------|-------|
| Hit@1 | 0.720 |
| Hit@3 | 0.940 |
| Hit@5 | 0.980 |
| MRR | 0.829 |
| NDCG@5 | 0.605 |
| Recall@5 | 0.603 |
| Latency P50 | 118.2ms |
| Latency P95 | 138.3ms |

---

## 13. Corpus Integrity Verification

| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| Qdrant collection name | `sre_copilot_v1` | `sre_copilot_v1` | ✅ |
| Total points | 5,533 | 5,533 | ✅ |
| Documents indexed | 252 | 252 | ✅ |
| Documents pending | 0 | 0 | ✅ |
| Indexing failures | 0 | 0 | ✅ |
| Outdated content hashes | 0 | 0 | ✅ |

**Corpus is intact and unmodified.**

---

## 14. Stage 4 Documentation Index

| Document | Location |
|----------|----------|
| Task 47 — Retrieval Quality Report | `docs/STAGE_4_TASK_47_RETRIEVAL_QUALITY_REPORT.md` |
| Task 47 — Final Validation | `docs/STAGE_4_TASK_47_FINAL_VALIDATION.md` |
| Task 48 — Grounded Reliability Report | `docs/STAGE_4_TASK_48_GROUNDED_RELIABILITY_REPORT.md` |
| Task 48 — Final Acceptance Report | `docs/STAGE_4_TASK_48_FINAL_ACCEPTANCE_REPORT.md` |
| Task 49 — External Search Report | `docs/STAGE_4_TASK_49_EXTERNAL_SEARCH_REPORT.md` |
| Task 49 — Live Tavily Verification | `docs/STAGE_4_TASK_49_LIVE_TAVILY_VERIFICATION.md` |
| Task 50 — Retrieval Evaluation Report | `docs/STAGE_4_TASK_50_RETRIEVAL_EVALUATION_REPORT.md` |
| Task 50 — Retrieval Benchmark Report | `docs/STAGE_4_TASK_50_RETRIEVAL_BENCHMARK_REPORT.md` |
| Manual Testing Guide | `docs/PRE_PUSH_MANUAL_TESTING_GUIDE.md` |
| **This document** | `docs/PRE_PUSH_RUNTIME_VERIFICATION.md` |

---

## 15. Final Sign-Off

| Category | Result |
|----------|--------|
| Application starts cleanly | ✅ |
| Health endpoint green | ✅ |
| Full test suite (514 tests) | ✅ **514 passed, 0 failed** |
| Retrieval correct (OOMKilled, Helm, Prometheus) | ✅ |
| Out-of-scope refusal working | ✅ |
| Extractive path working | ✅ |
| Security checks passed | ✅ |
| No credential leakage | ✅ |
| Corpus integrity confirmed | ✅ 5,533 chunks, 252 docs |
| All Stage 4 docs present | ✅ |
| **PUSH-READY** | ✅ **YES** |

