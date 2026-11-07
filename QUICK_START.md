# Quick Start Guide - Your RAG System is Running!

## Current Status ✅

- **Streamlit UI**: Running at http://127.0.0.1:8501
- **API Server**: Running at http://127.0.0.1:8001
- **Data Ingestion**: ⚠️ **NOT DONE YET** (0 chunks indexed)

## Issue with Alternative Model

The error you're seeing:
```
502 nvidia/llama-3.3-70b-instruct: model — Model 'llama-3.3-70b-instruct' is n… 
Retrying in 17s · attempt 7/10
```

This is happening because the system is trying to use an unavailable model. Your configuration has **Gemini** set as the provider, which is correct.

## Fix the Configuration

Your `.env` is already set to use Gemini:
- `LLM_PROVIDER=gemini`
- `GEMINI_API_KEY=<Your API Key>`
- `GEMINI_MODEL=gemini-3.6-flash`

The API needs to be restarted to pick up this configuration.

## Steps to Get Everything Working

### 1. Stop the Current API
```bash
# Find and kill the API process
ps aux | grep "advanced_rag.api.main" | grep -v grep | awk '{print $2}' | xargs kill
```

### 2. Update Streamlit to Use Port 8001

Edit `ui/streamlit_app.py` and change the API_BASE from port 8000 to 8001:
```python
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8001")
```

### 3. Ingest the Data (REQUIRED)

```bash
source venv/bin/activate
python3 -m advanced_rag.ingestion.cli --seed-sql
```

This will:
- Index 8 Kubernetes runbooks → ~49 chunks
- Create the SQLite operations database
- Take 10-30 seconds

### 4. Restart the API on Port 8001

```bash
source venv/bin/activate
export API_PORT=8001
python3 -m advanced_rag.api.main
```

### 5. Verify Everything Works

In another terminal:
```bash
# Check API health
curl http://localhost:8001/health

# Should show:
# "status": "ok"
# "indexed_chunks": 49  (not 0!)
# "model": "gemini-3.6-flash"
```

### 6. Refresh Streamlit

Go to http://127.0.0.1:8501 and click "Rerun" or refresh the page.

## Alternative: Using Gemini Directly Without API Issues

If the model configuration keeps causing issues, you can:

1. **Test Gemini directly first**:
```bash
python3 test_gemini.py
```

2. **Check the LLM client**:
```bash
source venv/bin/activate
python3 -c "
from advanced_rag.config import get_settings
from advanced_rag.llm.client import get_llm

settings = get_settings()
print(f'Provider: {settings.llm_provider}')
print(f'Model: {settings.gemini_model}')

llm = get_llm()
result = llm.complete('Say hello in 3 words')
print(f'Response: {result.text}')
"
```

## Why the nvidia/llama Error?

This error suggests either:
1. The API hasn't loaded the updated `.env` configuration
2. There's a Hugging Face model reference somewhere trying to use nvidia/llama-3.3-70b-instruct
3. The `LLM_PROVIDER` environment variable isn't being read correctly

The fix is to restart the API server with the environment properly loaded.

## Quick Test Commands

Once everything is running:

```bash
# Test retrieval only
curl -s http://localhost:8001/retrieve \
  -H 'content-type: application/json' \
  -d '{"query":"exit code 137","mode":"hybrid","rerank":true}' | python3 -m json.tool

# Test a question (after ingestion)
curl -s http://localhost:8001/ask \
  -H 'content-type: application/json' \
  -d '{"question":"Why did my pod exit with code 137?"}' | python3 -m json.tool
```

## Summary

**Right now you need to:**
1. ✅ Streamlit is running (port 8501)
2. ⚠️ API needs restart to use Gemini (currently using wrong model)
3. ⚠️ Data needs ingestion (0 chunks → should be 49)
4. ⚠️ Streamlit needs to point to port 8001

**After these fixes, you'll have:**
- Working Gemini-powered RAG system
- 49 Kubernetes runbook chunks indexed
- Full pipeline: HyDE → CRAG → Self-RAG → Guardrails
- Text2SQL with approval gates
- Semantic caching
