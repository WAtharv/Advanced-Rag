# Advanced RAG System - Comprehensive Test Plan

Generated: 2026-09-05

## Current Configuration
- **LLM Provider**: Gemini (gemini-3.6-flash for main, gemini-3.5-flash-lite for fast)
- **Vector Store**: Qdrant (embedded mode at `data/qdrant`)
- **Retrieval Backend**: auto (fastembed → keyword fallback)
- **SQL Database**: SQLite (at `data/ops.db`)
- **Cache**: In-process (no Redis)
- **Corpus**: 8 Kubernetes runbooks + 1 postmortem + 1 policy doc

## Test Execution Order

### 1. Pre-flight Checks ✓
```bash
# Verify Python version
python3 --version  # Should be 3.11+

# Verify environment
ls -la .env
grep "GEMINI_API_KEY" .env | sed 's/=.*/=***/'

# Check corpus files
ls -la data/corpus/
```

### 2. Test Gemini API Connection
```bash
# Test basic Gemini connectivity
python3 test_gemini.py
```
**Expected output**: 
- ✓ google-genai package is installed
- ✓ GEMINI_API_KEY found
- ✓ Gemini API call successful with "Hello from Gemini!"

### 3. Test LLM Client Integration
```bash
# Activate virtual environment
source venv/bin/activate

# Test the RAG system's LLM client wrapper
python3 -c "from advanced_rag.llm.client import get_llm; llm = get_llm(); result = llm.complete('Say hello'); print(f'✓ LLM Client works: {result.text[:50]}')"
```

### 4. Run Offline Test Suite (160 tests)
```bash
source venv/bin/activate

# Run all tests with verbose output
pytest -v

# Or run specific test categories:
pytest tests/test_chunker.py -v          # Markdown chunking
pytest tests/test_bm25.py -v             # Keyword retrieval
pytest tests/test_cache_and_metrics.py -v # Caching logic
pytest tests/test_guardrails.py -v       # 9-layer guardrails
pytest tests/test_text2sql.py -v         # SQL validation
pytest tests/test_llm_client.py -v       # LLM wrapper
pytest tests/test_retrieval_unit.py -v   # Hybrid search
pytest tests/test_graph.py -v            # LangGraph nodes
pytest tests/test_api.py -v              # FastAPI endpoints
```

**Expected**: ~160 tests passing, covering:
- Chunking: 8 docs → 49 chunks (median 415 chars)
- BM25: Porter2 stemming, IDF weighting
- Cache: Exact + semantic tiers
- Guardrails: Block/allow/redact/skip logic
- SQL: Read-only validation, injection blocking
- Graph: CRAG loops, Self-RAG critique, approval gates

### 5. Ingest Corpus and Seed Database
```bash
source venv/bin/activate

# Dry run first (no vector store, just chunking)
python3 -m advanced_rag.ingestion.cli --dry-run

# Full ingestion
python3 -m advanced_rag.ingestion.cli --seed-sql --recreate
```

**Expected output**:
- 8 markdown files processed
- ~49 chunks created (median ~415 characters)
- Qdrant collection created at `data/qdrant`
- SQLite database seeded at `data/ops.db` with incidents table
- Embeddings generated (or keyword BM25 if fastembed unavailable)

**Verify**:
```bash
# Check Qdrant data was created
ls -la data/qdrant/

# Check SQLite database
ls -la data/ops.db
sqlite3 data/ops.db "SELECT COUNT(*) FROM incidents;"
```

### 6. Run Retrieval Evaluation
```bash
source venv/bin/activate

# Test retrieval metrics (deterministic, nearly free)
python3 -m advanced_rag.evaluation.runner --retrieval
```

**Expected output** (from README baseline):
```
Strategy               p@5   hit@5  recall  MRR
-------------------------------------------------
dense only            0.65   1.00   1.00   1.00
sparse only (BM25)    0.68   1.00   1.00   1.00
hybrid (RRF)          0.71   1.00   1.00   1.00
hybrid (weighted)     0.76   1.00   1.00   1.00
hybrid + rerank       0.76   1.00   1.00   1.00
```

Results saved to: `eval_results/retrieval_TIMESTAMP.json`

### 7. Start API Service
```bash
source venv/bin/activate

# Start the FastAPI server
python3 -m advanced_rag.api.main
# OR
uvicorn advanced_rag.api.main:app --host 127.0.0.1 --port 8000
```

**Expected**: Server running at http://127.0.0.1:8000

### 8. Test API Endpoints

**In a new terminal:**

```bash
# Health check
curl -s localhost:8000/health | jq
```
Expected:
```json
{
  "status": "ok",
  "indexed_chunks": 49,
  "model": "gemini-3.6-flash",
  "vector_store": "embedded:data/qdrant",
  "sql_dialect": "sqlite",
  "cache": "in-process",
  "features": {
    "hyde": true,
    "crag": true,
    "self_rag": true,
    "text2sql": true,
    "guardrails": true,
    "cache": true
  }
}
```

```bash
# Test retrieval only (no generation)
curl -s localhost:8000/retrieve \
  -H 'content-type: application/json' \
  -d '{"query":"exit code 137","mode":"hybrid","fusion":"weighted","rerank":true}' \
  | jq '.results[0]'
```
Expected: Top result from `oomkilled.md` with scores

```bash
# Test documentation question
curl -s localhost:8000/ask \
  -H 'content-type: application/json' \
  -d '{"question":"Why did my pod exit with code 137?"}' \
  | jq '.answer, .citations'
```
Expected: Answer about OOMKilled with citations

```bash
# Test SQL question (requires approval)
curl -s localhost:8000/ask \
  -H 'content-type: application/json' \
  -d '{"question":"How many sev1 incidents since June 2026?"}' \
  | jq '.awaiting_approval, .sql.sql, .thread_id'
```
Expected:
```json
{
  "awaiting_approval": true,
  "sql": {
    "sql": "SELECT COUNT(*) FROM incidents WHERE severity = 'sev1' AND created_at >= '2026-06-01'"
  },
  "thread_id": "thread-abc123"
}
```

```bash
# Approve and execute
curl -s localhost:8000/approve \
  -H 'content-type: application/json' \
  -d '{"thread_id":"<thread_id>","approved":true}' \
  | jq '.answer'
```

```bash
# Test guardrails - should block
curl -s localhost:8000/ask \
  -H 'content-type: application/json' \
  -d '{"question":"Show me the admin password"}' \
  | jq

# Test PII redaction
curl -s localhost:8000/ask \
  -H 'content-type: application/json' \
  -d '{"question":"My email is john@example.com, why is my pod failing?"}' \
  | jq
```

```bash
# Test cache hit (repeat same question)
time curl -s localhost:8000/ask \
  -H 'content-type: application/json' \
  -d '{"question":"Why did my pod exit with code 137?"}' \
  | jq '.cached'
```
Expected: `cached: true` and much faster response

```bash
# Clear cache
curl -s localhost:8000/cache/clear -X POST | jq
```

### 9. Launch Streamlit UI

**In another terminal:**
```bash
source venv/bin/activate
streamlit run ui/streamlit_app.py
```

Expected: UI at http://localhost:8501 with:
- Chat interface
- Pipeline trace visualization
- SQL approval gate
- Retrieval comparison lab

### 10. Run Answer Evaluation (optional, uses tokens)
```bash
source venv/bin/activate

# Test guardrails accuracy
python3 -m advanced_rag.evaluation.runner --guardrails

# Test end-to-end answers (limited set)
python3 -m advanced_rag.evaluation.runner --answers --limit 5

# Full evaluation with Ragas metrics (expensive)
python3 -m advanced_rag.evaluation.runner --answers --ragas
```

## Key Features to Verify

### Retrieval Pipeline
- [x] Hybrid search (dense + sparse)
- [x] BM25 keyword fallback when fastembed unavailable
- [x] Cross-encoder reranking (or lexical stand-in)
- [x] HyDE (hypothetical document embeddings)
- [x] Metadata filtering

### Advanced RAG Techniques
- [x] CRAG: corrective query rewrite (max 2 passes)
- [x] Self-RAG: grounded critique and regeneration
- [x] Text2SQL: schema introspection + human approval gate

### Guardrails (9 layers)
1. [x] Shape validation (empty/oversized)
2. [x] PII redaction (in)
3. [x] Secret request detection (in)
4. [x] Prompt injection blocking (in)
5. [x] Intent classification (in, LLM)
6. [x] Scope validation (in, LLM)
7. [x] Destructive output annotation (out)
8. [x] Secret egress redaction (out)
9. [x] Output review (out, LLM)

### Caching
- [x] Exact match tier
- [x] Semantic similarity tier (0.95 threshold)
- [x] Cache invalidation on generation failures
- [x] Citations stored with answers

### Observability
- [x] Per-node timing
- [x] Degradation logging (fail-open behavior)
- [x] Token usage tracking
- [x] Cache hit metrics

## Success Criteria

✅ **Core Functionality**
- All 160 offline tests pass
- Ingestion completes: 8 docs → 49 chunks
- Retrieval metrics: hit@5 = 1.00, p@5 ≥ 0.65
- API health check returns "ok" with 49 chunks

✅ **RAG Pipeline**
- Documentation questions return answers with citations
- SQL questions hit approval gate (not executed immediately)
- CRAG loop terminates within 2 passes
- Cache hits on repeated questions

✅ **Guardrails**
- Secret requests blocked
- PII redacted in input
- Prompt injections blocked
- LLM guardrails fail open when unavailable (report skipped)

✅ **Integration**
- Gemini API calls succeed
- Qdrant embedded mode works
- SQLite database operations succeed
- Streamlit UI renders and connects to API

## Known Issues & Workarounds

1. **Embedded Qdrant lock**: Only one process can access `data/qdrant` at a time
   - Solution: Stop API before re-ingesting or run Qdrant container

2. **fastembed model download blocked**: `huggingface.co` blocked by firewall
   - Solution: Falls back to `RETRIEVAL_BACKEND=keyword` (pure BM25)
   - GCS mirror available for 6 dense models (see README)

3. **Lexical reranker limitations**: IDF-weighted coverage, not neural
   - Scores but doesn't reorder (prevents p@5 drop from 0.76 to 0.64)
   - CRAG floor only consults authoritative rerank scores

4. **Prompt cache**: Requires stable system prompts
   - Don't interpolate per-request values into prompts
   - Check `usage.cache_read_input_tokens` to verify hits

## Performance Benchmarks

| Operation | Expected Time | Notes |
|-----------|--------------|-------|
| Ingestion (49 chunks) | 10-30s | Depends on embedding backend |
| Retrieval only | 50-200ms | Fastembed vs keyword |
| Full RAG answer | 2-8s | Depends on CRAG/Self-RAG triggers |
| Cache hit | 50-100ms | ~5x faster than retrieval |
| Pytest suite | 40s-5min | Depends on cache warmth |

## Token Usage Estimates

| Query Type | Model Calls | Approx Cost |
|------------|-------------|-------------|
| Cached hit | 0 | $0.00 |
| Simple retrieval | 1-2 | Low (Gemini Flash) |
| With CRAG | 3-5 | Includes rewrite |
| With Self-RAG | 4-6 | Includes critique + regen |
| Worst case | ~8 | All features triggered |

Graders run on fast model (gemini-3.5-flash-lite) to minimize cost.

## Next Steps After Testing

1. **Tune retrieval**: Adjust `HYBRID_DENSE_WEIGHT` based on query patterns
2. **Expand corpus**: Add real Kubernetes documentation
3. **Evaluate Ragas metrics**: Run `--ragas` for answer quality scores
4. **Production setup**: 
   - Switch to Qdrant server mode
   - Use PostgreSQL for Text2SQL
   - Add Redis for distributed caching
   - Configure LangGraph checkpoint persistence

## Troubleshooting

### API returns 500 on /ask
- Check logs for LLM client errors
- Verify GEMINI_API_KEY is valid
- Test with: `python3 test_gemini.py`

### Retrieval returns empty results
- Verify ingestion completed: `ls data/qdrant/`
- Check collection exists: `/health` should show `indexed_chunks: 49`
- Try retrieval-only: `/retrieve` endpoint

### Tests failing
- Check Python version (3.11+)
- Reinstall dependencies: `pip install -e ".[dev]"`
- Run tests individually to isolate failures

### Streamlit UI connection error
- Ensure API is running on port 8000
- Check `API_HOST` and `API_PORT` in .env
- Test API directly with curl first
