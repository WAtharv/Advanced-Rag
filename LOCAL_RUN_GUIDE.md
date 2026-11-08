# Local Run & Startup Guide: Kubernetes SRE Copilot

This guide provides complete, battle-tested instructions for running the **Enterprise Advanced RAG / Kubernetes SRE Copilot** locally.

---

## 1. Executive Summary & Architecture

The Kubernetes SRE Copilot is a unified full-stack application:
- **Backend**: FastAPI with LangGraph state machine orchestrating hybrid dense/sparse retrieval, Reciprocal Rank Fusion (RRF), CrossEncoder reranking, Text2SQL with human-in-the-loop approvals, and guardrails.
- **Frontend**: High-performance, single-page application located in `ui/web/` (`index.html`, `app.js`, `styles.css`, `markdown.js`). It is **served directly by FastAPI** via static file mounts at `/`. **No separate Node.js, Vite, or Streamlit process is needed.**
- **Vector Database**: Embedded Qdrant running locally at `data/qdrant/` by default. **Docker is not required for local execution.**
- **Relational Database**: Local SQLite fallback at `data/ops.db` by default. Supports PostgreSQL when `POSTGRES_DSN` is configured.
- **Cache**: In-process memory cache by default. Supports Redis when `REDIS_URL` is configured.
- **Embeddings**: Local CPU FastEmbed using `BAAI/bge-small-en-v1.5` (384 dimensions) for dense semantic search and local BM25 for sparse lexical search.
- **LLM / Generation**: Hosted Groq API, local Ollama, or deterministic **Offline Extractive Mode** (which quotes verbatim runbook passages and citations without requiring external API keys or cloud access).

---

## 2. Prerequisites

- **Operating System**: Windows 10/11 (PowerShell 5.1+ or PowerShell Core 7+), Linux, or macOS.
- **Python**: Python 3.11+ (verified on Python 3.11.9).
- **Package Manager**: [uv](https://astral.sh/uv) (recommended) or standard `pip`.

---

## 3. Installation & Virtual Environment Setup

### Option A: Using `uv` (Fastest)

```powershell
# Clone or navigate to the repository
cd C:\Users\ASUS\advanced-rag

# Create virtual environment and install all dependencies
uv venv .venv --python 3.11
.venv\Scripts\Activate.ps1
uv pip install -e .
```

### Option B: Using Standard Python & `pip`

```powershell
# In PowerShell:
cd C:\Users\ASUS\advanced-rag
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Upgrade packaging tools and install in editable mode
python -m pip install --upgrade pip
pip install -e .
```

> [!NOTE]
> If PowerShell restricts script execution, run:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

---

## 4. Service Dependencies: Required vs. Optional

| Service | Mode | Default Local Configuration | Docker Required? | Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Python API** | Mandatory | Port 8000 (configurable via `API_PORT`) | **No** | Hosts both API endpoints and the Web UI |
| **Qdrant Vector DB** | Embedded / Local | `data/qdrant/` (`QDRANT_PATH`) | **No** | Single-process embedded database. Optional Docker: `docker compose up -d qdrant` |
| **Relational DB** | SQLite Fallback | `data/ops.db` | **No** | Stores document metadata and incident logs. Optional Postgres: `POSTGRES_DSN` |
| **Cache Store** | In-Process Memory | Local LRU dict (`REDIS_URL=""`) | **No** | In-memory semantic/response cache. Optional Redis: `docker compose up -d redis` |
| **Embeddings** | In-Process Local | `BAAI/bge-small-en-v1.5` + BM25 | **No** | Runs entirely on CPU via FastEmbed |
| **Reranker** | In-Process Local | `Xenova/ms-marco-MiniLM-L-6-v2` | **No** | Optional CrossEncoder reranking |
| **Language Model** | Offline / Groq | `LLM_PROVIDER="offline"` | **No** | Offline mode returns exact runbook passages and citations |

---

## 5. Environment Configuration & Runtime Overrides

The application reads configuration from `.env` and environment variables. Do **not** modify `.env` directly if you want to switch collections or LLM providers; use shell environment variables:

### Key Runtime Environment Variables

```powershell
# Select target Qdrant collection (default in production: sre_copilot_v1)
$env:QDRANT_COLLECTION = "sre_copilot_v1"

# Select LLM provider ('offline', 'groq', 'ollama')
# 'offline' enables zero-cloud, deterministic runbook retrieval with source citations
$env:LLM_PROVIDER = "offline"

# Hybrid retrieval fusion strategy ('rrf' or 'weighted')
$env:RETRIEVAL_HYBRID_FUSION = "rrf"

# Optional CrossEncoder reranking ('false' or 'true')
$env:CROSS_ENCODER_ENABLED = "false"

# API Port (default: 8000)
$env:API_PORT = "8000"
```

---

## 6. How to Start the Application

### Method 1: Python Module Execution (Recommended)

Open a PowerShell terminal, activate the virtual environment, and run:

```powershell
# 1. Activate virtual environment
.\.venv\Scripts\Activate.ps1

# 2. Set collection and offline mode overrides
$env:QDRANT_COLLECTION = "sre_copilot_v1"
$env:LLM_PROVIDER = "offline"

# 3. Start the FastAPI server
python -m advanced_rag.api.main
```

### Method 2: Console Script Entrypoint

```powershell
.\.venv\Scripts\rag-api
```

### Method 3: Direct Uvicorn Command

```powershell
.\.venv\Scripts\uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000
```

### Method 4: Automated PowerShell Helper

```powershell
.\run.ps1 -Port 8000
```

---

## 7. How to Access the Application

Once started, the server listens on `http://127.0.0.1:8000`:

| Resource | URL | Description |
| :--- | :--- | :--- |
| **Web Copilot UI** | [http://127.0.0.1:8000/](http://127.0.0.1:8000/) | Interactive SRE Copilot chat interface, incident responder, and runbook explorer |
| **Interactive API Docs** | [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) | Swagger UI for exploring and testing all endpoints |
| **OpenAPI Schema** | [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json) | Raw OpenAPI v3.1 specification |
| **Health Check** | [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) | Reports status, indexed chunks count, LLM mode, vector store, and SQL dialect |
| **Knowledge Stats** | [http://127.0.0.1:8000/api/knowledge/stats](http://127.0.0.1:8000/api/knowledge/stats) | Full knowledge inventory, category breakdowns, chunk counts, and source stats |
| **Document Registry** | [http://127.0.0.1:8000/api/knowledge/documents](http://127.0.0.1:8000/api/knowledge/documents) | Paginated registry of tracked knowledge base documents |
| **Ingestion Status** | [http://127.0.0.1:8000/api/knowledge/ingestion/status](http://127.0.0.1:8000/api/knowledge/ingestion/status) | Timestamps, durations, and status of recent synchronization runs |

---

## 8. Verifying End-to-End Operation

### Automated Runtime Test Script

Run the automated verification script to validate all endpoints, frontend static assets, and execute 5 representative RAG queries:

```powershell
.venv\Scripts\python scripts/verify_runtime_local.py
```

### Querying via PowerShell (`Invoke-RestMethod`)

#### 1. Health Check
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method GET | ConvertTo-Json
```

#### 2. RAG Copilot Query (`/ask`)
```powershell
$body = @{
    question = "Explain how to troubleshoot CrashLoopBackOff."
    use_llm = $false
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method POST -Body $body -ContentType "application/json"
$response | Select-Object route, extractive, citations
```

#### 3. Pure Vector / Hybrid Retrieval (`/retrieve`)
```powershell
$body = @{
    query = "How do I investigate an OOMKilled pod?"
    top_k = 5
    mode = "hybrid"
    fusion = "rrf"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8000/retrieve" -Method POST -Body $body -ContentType "application/json"
```

---

## 9. How to Stop the Application

To shut down the local server:
1. In the terminal running the application, press `Ctrl + C`.
2. Uvicorn will trigger the graceful shutdown sequence and release the embedded Qdrant and SQLite file locks.

---

## 10. Troubleshooting & Common Snags

### 1. Embedded Qdrant File Lock (`EmbeddedStoreBusyError`)
- **Symptom**: `RuntimeError: Storage folder data\qdrant is already accessed by another instance of Qdrant client.`
- **Cause**: Embedded Qdrant locks its directory exclusively per process. You cannot run `rag-api` and `rag-ingest` simultaneously against local files.
- **Solution**: Stop the background API server before running ingestion or evaluation scripts, or launch Qdrant in Docker (`docker compose up -d qdrant`) and set `$env:QDRANT_URL = "http://localhost:6333"`.

### 2. Groq Model Availability (HTTP 404 `model_not_found`)
- **Symptom**: `Groq call error: NotFoundError: Error code: 404 - {'error': {'message': "The model 'llama-3.3-70b-versatile' does not exist..."}}`
- **Cause**: The configured Groq API key does not have permissions for the requested model tier.
- **Solution**: Set `$env:LLM_PROVIDER = "offline"`. The pipeline gracefully operates in extractive mode, returning exact quotes and source citations without crashing.

### 3. Port 8000 in Use
- **Symptom**: `[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)`
- **Solution**: Set `$env:API_PORT = "8001"` and run `python -m advanced_rag.api.main`, or pass `--port 8001` to Uvicorn.

