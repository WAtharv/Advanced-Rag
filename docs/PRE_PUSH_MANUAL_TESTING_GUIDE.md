# Pre-Push Manual Testing Guide
## Enterprise Advanced RAG / Kubernetes SRE Copilot

> **Important:** Run all commands from the project root: `c:\Users\ASUS\advanced-rag`

---

## 1. Prerequisites

| Requirement | Value |
|-------------|-------|
| Python version | 3.11.9 |
| Virtual environment | `.venv\` |
| Qdrant embedded storage | `data\qdrant\` |
| Config file | `.env` (copy from `.env.example`) |
| Required keys | `GROQ_API_KEY`, `TAVILY_API_KEY` |

---

## 2. Environment Setup

```powershell
# From project root
cd c:\Users\ASUS\advanced-rag

# Activate virtual environment
.venv\Scripts\Activate.ps1

# Verify Python version (must be 3.11.x)
.venv\Scripts\python --version
# Expected: Python 3.11.9

# Verify core imports
.venv\Scripts\python -c "import fastapi, qdrant_client, langgraph, fastembed; print('All OK')"
# Expected: All OK
```

---

## 3. Run Automated Tests

> **Note:** The asyncio deprecation warning that causes PowerShell exit code 1 is benign.
> This is a known `pytest-asyncio` v0.24 behavior. Tests are not failing when this warning appears.

> **Important:** Run tests with the **API server stopped**. The embedded Qdrant storage
> can only be held by one process. Running the server and tests simultaneously causes
> `EmbeddedStoreBusyError` in the lifecycle and sync tests.

### Full Test Suite

```powershell
.venv\Scripts\pytest -q --tb=short
# Expected: 514 passed, 0 failed, 0 errors
```

### Stage-by-Stage Breakdown

```powershell
# Task 47 — Retrieval Quality (17 tests, ~8s)
.venv\Scripts\pytest tests/test_stage4_task47.py -v

# Task 48 — Grounded Reliability (36 tests, ~5s)
.venv\Scripts\pytest tests/test_stage4_task48.py -v

# Task 49 — External Search Fallback (47 tests, ~6s)
.venv\Scripts\pytest tests/test_stage4_task49.py -v

# Task 50 — Retrieval Benchmarking (10 tests, ~3s)
.venv\Scripts\pytest tests/test_stage4_task50.py -v

# Retrieval unit tests (36 tests)
.venv\Scripts\pytest tests/test_retrieval_unit.py tests/test_retrieval_lab_fixes.py tests/test_sre_copilot_retrieval.py -v

# API and graph tests (43 tests)
.venv\Scripts\pytest tests/test_api.py tests/test_graph.py -v

# Document lifecycle and sync (35 tests - requires server stopped)
.venv\Scripts\pytest tests/test_document_lifecycle_and_sync.py tests/test_knowledge_stats_and_registry.py -v
```

---

## 4. Start the Application

> **Warning:** Only one process may hold embedded Qdrant at a time.
> Do NOT run ingestion scripts while the API server is running.
> Do NOT run tests that open Qdrant directly while the server is running.

### Start the API Server

```powershell
# Method 1: Using the installed script (recommended)
.venv\Scripts\rag-api

# Method 2: Using uvicorn directly
.venv\Scripts\uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000 --log-level info
```

**Expected startup output:**
```
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
INFO     advanced_rag.certs | TLS verification is using the system trust store
INFO     advanced_rag.api.main | Starting API (model=qwen/qwen3.8-27b)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

> **Tip:** First startup compiles the LangGraph pipeline — allow 8-10 seconds.
> Subsequent requests are fast (uses `lru_cache`).

---

## 5. Frontend & Swagger UI

| URL | Purpose |
|-----|---------|
| `http://127.0.0.1:8000` | Web chat frontend |
| `http://127.0.0.1:8000/docs` | FastAPI Swagger UI |
| `http://127.0.0.1:8000/redoc` | FastAPI ReDoc |

**Frontend features to test:**
1. Type a Kubernetes question and press **Enter** → verify LLM answer with citations
2. Toggle **"Without LLM"** → verify extractive quoted-passage answer
3. Use the **Retrieval Lab** panel → compare dense / sparse / hybrid modes
4. View the **Knowledge Base** panel → verify 252 documents, 5,533 chunks

---

## 6. API Manual Verification

### Health Check

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" | ConvertTo-Json
```

**Expected:**
```json
{
  "status": "ok",
  "indexed_chunks": 5533,
  "llm_mode": "live",
  "model": "qwen/qwen3.8-27b",
  "features": { "hyde": false, "crag": true, "self_rag": false, "text2sql": true, "guardrails": true }
}
```

---

### Scenario A — OOMKilled Diagnosis (Internal Knowledge)

```powershell
$body = '{"question": "A container was terminated with exit code 137. How do I determine whether it was OOMKilled and inspect the memory limit?"}'
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method Post -ContentType "application/json" -Body $body
Write-Host "Answer length: $($r.answer.Length) chars"
Write-Host "Thread ID: $($r.thread_id)"
$r.answer.Substring(0, [Math]::Min(500, $r.answer.Length))
```

**Expected:** Structured kubectl runbook answer, no `awaiting_approval`, answer references OOMKilled symptoms.

---

### Scenario B — Helm Chart Debugging

```powershell
$body = '{"question": "How do I debug a failed Helm release upgrade and rollback to the previous version?"}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method Post -ContentType "application/json" -Body $body | Select-Object answer, sources
```

**Expected:** Answer with `helm history`, `helm rollback` commands citing Helm runbooks.

---

### Scenario C — Out-of-Scope Refusal (Router Test)

```powershell
$body = '{"question": "How do I configure quantum teleportation overlay networks in kube-proxy v2.99?"}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method Post -ContentType "application/json" -Body $body | Select-Object answer
```

**Expected:** `"That question is outside what this assistant covers."` — no hallucination, no retrieval.

---

### Scenario D — Extractive Answer (Without LLM)

```powershell
$body = '{"question": "What are the standard Helm chart hooks and their execution order?", "use_llm": false}'
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method Post -ContentType "application/json" -Body $body
$r.answer.Substring(0, 300)
```

**Expected:** Answer begins with `**No language model is configured, so this answer is extractive.**`

---

### Scenario E — Multi-Turn Conversation

```powershell
# First turn — establish context
$body1 = '{"question": "What is a PodDisruptionBudget?", "thread_id": null}'
$r1 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method Post -ContentType "application/json" -Body $body1
$tid = $r1.thread_id
Write-Host "Thread ID: $tid"

# Second turn — same thread, follow-up question
$body2 = "{`"question`": `"How do I create one with minimum availability of 2?`", `"thread_id`": `"$tid`"}"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method Post -ContentType "application/json" -Body $body2 | Select-Object answer
```

**Expected:** Follow-up answer maintains context from first turn.

---

## 7. Retrieval Lab API Verification

### Hybrid RRF (Default Production Mode)

```powershell
$body = '{"query": "pod OOMKilled memory limit exit code 137", "mode": "hybrid", "top_k": 5}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/retrieve" -Method Post -ContentType "application/json" -Body $body | Select-Object query, mode, result_count, latency_ms
```

**Expected:** `result_count: 5`, `mode: hybrid`, `latency_ms.total < 600ms`

### Dense Retrieval

```powershell
$body = '{"query": "pod OOMKilled memory limit", "mode": "dense", "top_k": 5}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/retrieve" -Method Post -ContentType "application/json" -Body $body | Select-Object result_count, latency_ms
```

**Expected:** `result_count: 5`, `latency_ms.total < 100ms`

### Sparse (BM25) Retrieval

```powershell
$body = '{"query": "CrashLoopBackOff exit code 137", "mode": "sparse", "top_k": 5}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/retrieve" -Method Post -ContentType "application/json" -Body $body | Select-Object result_count, mode
```

### Category-Filtered Retrieval

```powershell
$body = '{"query": "node affinity taints tolerations", "mode": "hybrid", "top_k": 10, "category": "kubernetes"}'
Invoke-RestMethod -Uri "http://127.0.0.1:8000/retrieve" -Method Post -ContentType "application/json" -Body $body | Select-Object result_count, category
```

**Expected:** Results exclusively from the `kubernetes` corpus category.

---

## 8. Knowledge Base API Verification

```powershell
# Overall stats — must show 252 docs, 5533 chunks
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/knowledge/stats" | Select-Object -ExpandProperty indexing

# Document list — must count 252 built-in entries
Invoke-RestMethod -Uri "http://127.0.0.1:8000/documents" | Measure-Object | Select-Object Count

# Ingestion status
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/knowledge/ingestion/status" | ConvertTo-Json

# Categories summary
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/knowledge/categories" | Measure-Object | Select-Object Count
```

---

## 9. Security Checks

```powershell
# .env must NOT be accessible (should return 404)
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:8000/.env" | Out-Null
    Write-Host "FAIL: .env is publicly accessible!"
} catch {
    Write-Host "PASS: .env blocked (HTTP $($_.Exception.Response.StatusCode.value__))"
}

# Health response must not contain API key patterns
$health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" | ConvertTo-Json -Depth 10
if ($health -match "gsk_|sk-|tvly-") {
    Write-Host "SECURITY FAIL: API key found in response!"
} else {
    Write-Host "SECURITY PASS: No API keys leaked in health response"
}
```

---

## 10. Common Issues and Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| `EmbeddedStoreBusyError` in tests | API server holding Qdrant lock | Stop server before running lifecycle/sync tests |
| Port 8000 already in use | Previous server still running | `netstat -ano \| findstr :8000` then kill the PID |
| `ModuleNotFoundError: advanced_rag` | Wrong working directory | `cd c:\Users\ASUS\advanced-rag` |
| Slow first request (~6-10s) | LangGraph cold-start compilation | Expected; subsequent requests fast |
| Groq rate limit error | Rapid successive requests | Wait 60s; free tier has rate limits |
| `pytest` exit code 1 with asyncio warning | pytest-asyncio v0.24 known issue | Benign; ignore if all tests pass |
| `/api/knowledge/categories` shows "empty" | Registry vs Qdrant naming mismatch | Known cosmetic issue; retrieval works correctly |

---

## 11. Stopping the Server

```powershell
# Press Ctrl+C in the terminal running uvicorn
# OR find and kill the process:
Get-Process python | Where-Object { $_.MainWindowTitle -match "uvicorn" } | Stop-Process
```

---

## 12. Final Checklist

- [ ] Python 3.11.9 confirmed
- [ ] `import fastapi, qdrant_client, langgraph, fastembed` → OK
- [ ] Full test suite: **514 passed**, 0 failed (server stopped)
- [ ] Server starts cleanly on port 8000 (< 10s)
- [ ] `/health` → `status: ok`, `indexed_chunks: 5533`, `llm_mode: live`
- [ ] `/ask` OOMKilled query → structured LLM answer with citations
- [ ] `/ask` out-of-scope → refusal (no hallucination)
- [ ] `/ask` `use_llm: false` → extractive quoted passages
- [ ] `/retrieve` hybrid → 5 relevant results < 600ms
- [ ] `/documents` → 252 entries
- [ ] `/api/knowledge/stats` → `indexed_documents_count: 252`, `total_indexed_chunks: 5533`
- [ ] `.env` URL → 404 (not accessible)
- [ ] No API keys in any response body

