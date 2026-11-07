# AdvancedRAG: End-to-End Workflow Analysis

This repository is an enterprise-grade Kubernetes operations RAG system built with Python, FastAPI, LangGraph, Qdrant, PostgreSQL/SQLite, Redis, and LLMs (Anthropic/Gemini, or offline mode). The project’s purpose is to answer Kubernetes/SRE troubleshooting questions using a hybrid retrieval pipeline, optionally generate SQL against an ops database, enforce guardrails, and surface everything through a web UI and REST API.

## 1. Repository shape and runtime stack

The project is organized around a Python package at `src/advanced_rag/` with these major subsystems:

- `config.py`: app-wide settings and environment-backed configuration
- `api/main.py`: FastAPI app, routes, static UI mount
- `graph/`: LangGraph orchestration, state, node logic, control flow
- `retrieval/`: embeddings, BM25, hybrid vector store, reranking, HyDE
- `ingestion/`: corpus loader and markdown chunker for `rag-ingest`
- `text2sql/`: schema introspection, SQL generation, validation, execution
- `guardrails/`: deterministic and model-based safety gates
- `llm/`: model client wrapper, prompts, extractive fallback
- `cache.py`: exact + semantic answer cache
- `models.py`: shared Pydantic models
- `ui/web/`: static HTML/CSS/JS frontend
- `data/corpus/`: Markdown runbooks used as the knowledge base
- `tests/`: offline validation suite

The project uses:

- Python 3.11+
- LangGraph for orchestration
- FastAPI + Uvicorn for HTTP API + UI
- Qdrant for vector search
- Pure Python BM25 (`keyword` mode) and/or fastembed/Gemini embeddings
- PostgreSQL when `POSTGRES_DSN` is set, otherwise SQLite
- Redis when `REDIS_URL` is set, otherwise in-memory cache
- Anthropic API as the default LLM backend; Gemini is also supported
- optional offline mode: no LLM, extractive answer from retrieved passages

---

## 2. Startup path: from process launch to app readiness

### 2.1 Entry points

The package declares two main console scripts in `pyproject.toml`:

- `rag-ingest = "advanced_rag.ingestion.cli:main"`
- `rag-api = "advanced_rag.api.main:run"`

So the app can start in two modes:

- Ingestion: `rag-ingest --seed-sql`
- API/UI: `rag-api`

### 2.2 Config loading

`src/advanced_rag/config.py` creates a `Settings` object via `pydantic-settings`:

- It reads `.env` from the project root
- It exposes values such as:
  - `llm_provider`
  - `llm_model`, `llm_fast_model`
  - `qdrant_url`, `qdrant_path`
  - `postgres_dsn`, `sqlite_path`
  - `redis_url`
  - `retrieve_top_k`, `rerank_top_n`
  - feature flags for HyDE, CRAG, Self-RAG, Text2SQL, guardrails, cache
  - `api_host`, `api_port`

Important config behavior:

- If `qdrant_url` is not set, it uses embedded Qdrant on disk at `data/qdrant`
- If `postgres_dsn` is not set, it uses SQLite at `data/ops.db`
- If `redis_url` is not set, the answer cache uses in-process memory

`Settings.sql_url` is the central factory for the SQL backend, and `Settings.use_remote_qdrant` decides whether to use remote or embedded Qdrant.

### 2.3 API bootstrap

The app starts in `src/advanced_rag/api/main.py`:

- `run()` calls `uvicorn.run("advanced_rag.api.main:app", ...)`
- `app = FastAPI(...)`
- `lifespan(app)` does:
  - `settings = get_settings()`
  - `setup_logging(settings.log_level)`
  - `enable_system_trust_store()`
  - `pipeline.get_graph()` to compile the LangGraph once at startup so the first request does not pay compile cost

It also mounts the UI:

- `_web_dir()` searches for static assets under `advanced_rag/api/web` or `ui/web`
- If found, it mounts:
  - `/` serves `index.html`
  - static JS/CSS assets are served as files
- That means the API and UI are in one process

---

## 3. The LLM layer: capability detection and model clients

The repo intentionally abstracts LLM usage behind `src/advanced_rag/llm/client.py`.

### 3.1 Provider selection

`get_llm()` does:

- if `LLM_PROVIDER=offline` -> raise `LLMUnavailableError`
- if `LLM_PROVIDER=gemini` -> return `GeminiClient`
- otherwise -> return `LLMClient`

`llm_available()` caches a single boolean result:
- it tries to construct the model client once
- if construction fails, it logs a warning and returns `False`
- this prevents repeated startup failures from appearing on every graph node

### 3.2 Anthropic client wrapper

`LLMClient.complete(...)` builds Anthropic Messages API payloads and supports:

- prompt caching for frozen system prompts
- `output_config.effort` on supported models
- structured JSON output via Pydantic schema
- `complete_json()` for schema-constrained classification and grading
- refusal fallback support when the API model supports it

`llm.prompts` defines the frozen system prompts:
- `ANSWER_SYSTEM`
- `ROUTER_SYSTEM`
- `HYDE_SYSTEM`
- `CRAG_GRADER_SYSTEM`
- `QUERY_REWRITE_SYSTEM`
- `SELF_RAG_SYSTEM`
- `TEXT2SQL_SYSTEM`
- `SQL_ANSWER_SYSTEM`
- `GUARDRAIL_INTENT_SYSTEM`
- `OUTPUT_SAFETY_SYSTEM`

The key design point: prompt strings are module constants so prompt caching remains stable.

### 3.3 Gemini path

The project supports Gemini through `src/advanced_rag/llm/gemini_client.py` and the retrieval embedding path through `GeminiEmbedder` / `GeminiReranker` in `retrieval/embeddings.py`.

This is especially important because Hugging Face model downloads may fail on corporate networks; the repo supports “Gemini as dense backend” to bypass the ONNX fastembed dependency.

---

## 4. Ingestion pipeline: from markdown docs to Qdrant index

The corpus is built alongside the ingestion CLI.

### 4.1 CLI entry

`src/advanced_rag/ingestion/cli.py`:

- `build_parser()` defines:
  - `--corpus`: corpus directory, default `data/corpus`
  - `--recreate`: drop existing collection
  - `--dry-run`: chunk only, do not write
  - `--seed-sql`: also seed SQL database
- `main()`:
  - loads settings
  - calls `setup_logging`
  - calls `enable_system_trust_store()`
  - `documents = load_corpus(args.corpus)`
  - `chunks = chunk_corpus(documents)`
  - `store.ensure_collection(recreate=args.recreate)`
  - `written = store.upsert(chunks)`
  - optionally seeds SQL with `seed_database()`

### 4.2 Corpus loader

`src/advanced_rag/ingestion/loader.py`:

- `parse_front_matter()` splits YAML-ish front matter from a markdown document:
  - lines before `---` are metadata
  - body starts after the closing `---`
- `load_document(path, root)` reads a file and returns a `Document`
- `load_corpus(directory)` recursively finds all `.md` files under `data/corpus`
- `chunk_corpus(documents)` calls `chunk_document(...)` for each document

### 4.3 Chunking logic

`src/advanced_rag/ingestion/chunker.py` is deliberately markdown-aware:

- `split_sections(markdown)` breaks a document by headings while keeping the section title with the content
- `window(text, size, overlap)` chunks long sections on sentence boundaries
- `chunk_document(...)` creates `Chunk` objects with:
  - `id`
  - `text`
  - `source`
  - `title`
  - `section`
  - `doc_type`
  - `metadata`

`Chunk.payload()` builds the Qdrant payload:
- `chunk_id`, `text`, `source`, `title`, `section`, `doc_type`, plus custom metadata

The chunk ID is hash-based and content-addressed:
- `source + section + body` hashed into a stable ID
- re-ingesting unchanged docs overwrites the same point

This is important because it makes I/O idempotent and allow reindexing without duplicates.

### 4.4 Store indexing

`src/advanced_rag/retrieval/vectorstore.py` contains the Qdrant store.

`HybridStore.__init__()` chooses:
- remote Qdrant if `QDRANT_URL` is configured
- embedded Qdrant if not

`HybridStore.client` creates the Qdrant client.

`HybridStore.ensure_collection()`:
- checks if the collection exists
- creates either:
  - dense + sparse vectors if the embedder supports dense
  - sparse-only if not
- for remote Qdrant, creates payload indexes for fields like `doc_type` and `source`

`HybridStore.upsert(chunks)`:
- calls `embedder.sparse_documents(texts)`
- calls `embedder.embed_documents(texts)` if dense is available
- packs each `Chunk` into a Qdrant `PointStruct`
- sends batches to `client.upsert(...)`

The actual dimensionality and retrieval backend selection are handled by `retrieval/embeddings.py`.

---

## 5. Retrieval backend: dense, sparse, hybrid, rerank

### 5.1 Embedding selection

`src/advanced_rag/retrieval/embeddings.py` centralizes retrieval model loading.

`get_embedder()` decides backend:

- `keyword`: uses `KeywordEmbedder`
  - sparse-only BM25, no dense arm, no download
- `gemini`: uses `GeminiEmbedder`
  - dense from Gemini API
  - sparse from local BM25
- `fastembed` or `auto`:
  - tries `Embedder`
  - uses fastembed dense + sparse models
  - if dense model unavailable, it falls back to `KeywordEmbedder`

Important behaviors:

- `SparseVector` is the Qdrant sparse vector representation
- `Embedder._sparse_impl()` tries the fastembed sparse model, but falls back to local BM25 if unavailable
- the dense and sparse failure modes are isolated; they degrade independently

`get_reranker()` chooses:
- lexical fallback if no dense/rerank models are available
- `GeminiReranker` if using Gemini backend
- `Reranker` if the fastembed cross-encoder is available
- otherwise `LexicalReranker`

### 5.2 Retrieval logic

`src/advanced_rag/retrieval/retriever.py` defines `Retriever`.

`Retriever.retrieve(...)` does:

1. Determine whether HyDE is enabled
2. If `use_hyde`:
   - call `generate_hyde(question)`
   - append the HyDE passage to the real query: `question + "\n\n" + hyde_document`
3. Call `self.store.search(...)`
4. Optionally rerank the results with `self.rerank(...)`

`HybridStore.search(...)` chooses:

- `dense`: dense-only vector search
- `sparse`: sparse-only BM25 search
- `hybrid`:
  - `fusion="weighted"` -> client-side fusion using normalized scores
  - `fusion="rrf"` -> server-side reciprocal rank fusion

Weighted hybrid logic:
- fetch a pool of results from dense and sparse arms
- min-max normalize each arm’s scores
- combine with `HYBRID_DENSE_WEIGHT`
- produce a fused ranking

`_single()` runs one vector query with Qdrant:
- `query_points(collection_name=..., query=vector, using=using, limit=top_k...)`

`_hybrid_rrf()` uses `FusionQuery(fusion=models.Fusion.RRF)`

`_hybrid_weighted()` is the custom weighted fusion used by the repo for stronger performance on paraphrased troubleshooting questions.

### 5.3 Reranking

`Retriever.rerank(question, chunks)`:

- takes the candidate documents
- calls the reranker on the question and candidate passages
- stores `rerank_score`
- marks `rerank_is_authoritative` if the score came from a real cross-encoder
- if reranker is only lexical, it keeps the original retrieval order and does not reorder

This is a crucial design decision: the lexical fallback is intentionally not trusted enough to override retrieval order because the repo measured that it reduces precision.

`format_context(chunks)` turns the selected chunk list into numbered citation blocks:
- `[1] source - section`
- followed by the passage text

This is the exact context string fed to the answer-generation model.

---

## 6. Graph orchestration: the end-to-end LangGraph control flow

The core pipeline lives in `src/advanced_rag/graph/`.

### 6.1 State model

`graph/state.py` defines `RagState` as a `TypedDict`.

It tracks:
- input question + original question
- guardrail outcomes + block state
- cache status
- selected route (`vector`, `sql`, `both`, `reject`)
- retrieved chunks + context
- HyDE passage
- CRAG verdict and rewrites
- generated answer
- critique from Self-RAG
- SQL proposal + executed rows
- citations
- `trace` list for UI pipeline display
- token counts

`initial_state(question, force_extractive=False)` seeds the object with safe default values.

### 6.2 Graph builder

`graph/builder.py` builds a `StateGraph(RagState)`.

Important conditional edges:

- `guardrail_input -> cache_lookup`
- `cache_lookup -> route` or `finalize` if cached
- `route -> retrieve` for doc questions
- `route -> sql_generate` for SQL questions
- `route -> end` for rejected questions
- retrieval branch:
  - `retrieve -> grade_context`
  - `grade_context -> generate` if good
  - `grade_context -> rewrite_query` if weak
  - `rewrite_query -> retrieve` looped up to a max of two attempts
- generation branch:
  - `generate -> self_critique`
  - self critique either returns to `generate` or proceeds to `guardrail_output`
- output:
  - `guardrail_output -> finalize -> END`

There are two major “branch families”:

- document pipeline:
  - guardrails -> cache -> routing -> retrieval -> CRAG loop -> generation -> Self-RAG -> output guardrails -> finalize
- SQL path:
  - route decides SQL needed -> `sql_generate` -> `sql_approval` (interrupt) -> `sql_execute` -> generation

### 6.3 Graph public API

`src/advanced_rag/graph/pipeline.py` exposes the public methods:

- `ask(question, thread_id=None, force_extractive=False, graph=None)`
- `resume(thread_id, approved=True)`
- `pending_approval(thread_id)`

`ask()`:
- creates a new `thread_id` if not supplied
- builds a config with `thread_id` and recursion limit
- calls `graph.invoke(initial_state(...))`
- converts the final graph state to `AnswerResponse`

`resume(thread_id, approved=...)`:
- loads the graph state from the checkpointer
- calls `graph.invoke(Command(resume={"approved": approved}), config=config)`

This is where the human approval gate sits:
- the graph pauses at `sql_approval_node`
- the API returns `awaiting_approval=True`
- the caller sends `POST /approve` to continue

---

## 7. The request lifecycle from `POST /ask` to final answer

The API route is in `src/advanced_rag/api/main.py`.

### 7.1 `/health`

`GET /health`:
- calls `get_store().count()`
- reports `indexed_chunks`
- calls `llm_available()`
- returns:
  - status
  - llm mode (`live` vs `offline`)
  - model name
  - vector store address
  - SQL dialect
  - cache backend
  - feature flags

### 7.2 `/ask`

`POST /ask` expects:

```json
{
  "question": "Why did my pod exit with code 137?",
  "thread_id": "optional",
  "use_llm": true
}
```

The route calls:

```python
pipeline.ask(question, thread_id=request.thread_id, force_extractive=not request.use_llm)
```

This triggers the graph.

### 7.3 `/retrieve`

`POST /retrieve` is for retrieval-only tuning:
- accepts `query`, `mode`, `fusion`, `use_hyde`, `rerank`
- invokes `get_retriever().retrieve(...)`
- returns a JSON payload of candidate passages with retrieval and rerank scores

### 7.4 `/approve`

`POST /approve` resumes the graph after a SQL approval interrupt:
- `pipeline.resume(thread_id, approved=request.approved)`

### 7.5 `/pending/{thread_id}`

This checks if the graph is waiting for approval.

---

## 8. Guardrails: deterministic and model-based filters

The repo’s security layer is a nine-part pipeline in `src/advanced_rag/guardrails/pipeline.py`.

### 8.1 Inbound layers (before retrieval spend)

`Guardrails.check_input(question)`:

1. `shape`
   - reject empty question
   - reject oversize question > 4,000 chars
2. `pii_redaction`
   - redact email, IPv4, SSNs, credit cards
   - continue
3. `secret_request`
   - block requests to reveal secrets or credentials
4. `injection`
   - block prompt injection attempts
5. `intent`
   - LLM classifier decides if the request violates platform policy
6. `scope`
   - LLM classifier decides if it is in scope for this Kubernetes operations assistant

Important:
- The first four are deterministic regex-based checks
- The last two are LLM-backed and fail open with `action="skip"` if the model is unavailable
- `skip` is reported as skip, never as pass

They intentionally do not silently succeed if the model is unavailable:
- `passed=False`, `action="skip"`, detail indicates whether it was offline mode or classifier outage

`GuardrailResult.record()` stores outcomes and sets `blocked=True` if any layer blocked.

### 8.2 Deterministic patterns

`guardrails/patterns.py` contains:
- `SECRET_PATTERNS`: AWS keys, private keys, bearer tokens, JWTs, etc.
- `PII_PATTERNS`
- `SECRET_REQUEST_PATTERNS`
- `INJECTION_PATTERNS`
- `DESTRUCTIVE_PATTERNS`
- `SQL_WRITE_PATTERNS`

Those patterns are used both for:
- input sanitization
- output sanitization
- SQL validation

### 8.3 Outbound layers

`Guardrails.check_output(answer, question, context)` does:

7. `destructive_output`
   - if the answer contains `kubectl delete`, `helm uninstall`, etc., it adds a change-control notice
8. `secret_egress`
   - redact secrets found in generated text
9. `output_review`
   - LLM reviews if the answer is grounded, safe, and not claiming unsupported facts

If an LLM is unavailable, layer 9 is skipped rather than treating the answer as passed.

---

## 9. Cache path: exact + semantic answer cache

`src/advanced_rag/cache.py` implements two tiers:

- exact tier:
  - hashes normalized question
  - stores answer and metadata under `rag:exact:<hash>`
- semantic tier:
  - stores embeddings of prior questions
  - similarity lookup using cosine similarity
  - only used if an embedder is available

`AnswerCache.lookup(question)`:
- checks the exact tier first
- if no exact hit, tries semantic tier
- if payload exists and mode mismatches (`extractive` vs generated), it does not serve it

`AnswerCache.store(question, payload)`:
- stores answer, context, route, citations
- stores `extractive: True/False`
- caches only if the request was not blocked and not a failed generation

The repo explicitly forbids caching:
- blocked answers
- generation failures
- live SQL results

This is important because a transient LLM outage must not poison the cache for the whole TTL.

---

## 10. Guardrail input node, cache lookup node, route node

These are the concrete graph operations in `src/advanced_rag/graph/nodes.py`.

### 10.1 `guardrail_input_node`

This runs the first six guardrail layers against the request.

Returns:
- `question`: sanitized question
- `guardrails`: list of outcomes
- `blocked`
- `block_message`
- `answer` if blocked
- `trace`

### 10.2 `cache_lookup_node`

This tries:
- exact lookup
- semantic lookup
- rejects mismatched answer modes

If a cache hit is found:
- the graph short-circuits to `finalize`
- the state carries `cached=True`, `cache_kind`, `answer`, `context`, `citations`

### 10.3 `route_node`

This decides whether the question belongs to:

- `vector`
- `sql`
- `both`
- `reject`

Logic:

- if `enable_text2sql` is false -> force vector
- if no LLM is available -> force vector
- otherwise call `get_llm().complete_json(...)` with `RouteDecision`
- if the router fails, it defaults to vector for safety

If the route is `REJECT`, it sets a block state with a message.

---

## 11. Retrieval node and CRAG loop

### 11.1 `retrieve_node`

This does:

- `retriever = get_retriever()`
- if there are rewrite queries from a prior failed pass:
  - call `retriever.retrieve_multi(rewrites, question=original_question)`
- else:
  - `result = retriever.retrieve(question, use_hyde=settings.enable_hyde)`
  - `chunks = result.chunks`
  - `hyde_doc = result.hyde_document`

It then stores:
- `chunks`
- `context`
- `hyde_document`
- `retrieval_attempts`
- `trace`

### 11.2 `grade_node`

This is the CRAG relevance gate.

It checks:
- if no chunks were returned -> `incorrect`
- if the reranker has authoritative scores:
  - compute top score
  - if it is below `CRAG_RELEVANCE_FLOOR`, mark context as insufficient
- if no LLM is available:
  - accept context unjudged (`correct`)
- otherwise call an LLM grader using `ContextGrade` schema:
  - `correct`, `ambiguous`, or `incorrect`

This is the exact moment when the repo chooses to be conservative with retrieval quality:
- the lexical rerank score is not treated as authoritative
- only a real cross-encoder or strong ranking signal decides if the context is too weak

### 11.3 `rewrite_node`

If the context was weak, it proposes alternative search queries with:
- `rewrite_query(question)`
- model uses `QUERY_REWRITE_SYSTEM`
- returns 3 alternative queries
- retrieval runs again with those rewrites

This is the corrective retrieval loop.

---

## 12. Generation node and Self-RAG critique

### 12.1 `generate_node`

This is the main answer-generation step.

It composes a prompt with:
- the original question
- retrieved context blocks
- optional SQL rows
- instructions if the context was judged insufficient
- self-RAG critique information if prior pass failed

Then:
- if `force_extractive` or no LLM available:
  - it uses `extractive_answer(...)`
  - returns a quoted passage answer
  - sets `extractive=True`
- otherwise:
  - calls `get_llm().complete(...)`
  - supports the model output and token accounting
  - handles `refused` cases
  - logs infrastructure failures separately

`extractive_answer()` lives in `src/advanced_rag/llm/extractive.py` and quotes the top chunks with citations, rather than synthesizing novel text.

### 12.2 `critique_node`

This is the Self-RAG reviewer.

It evaluates:
- whether claims are grounded in the context
- whether the answer addresses the question
- whether citations are present

If unacceptable:
- it returns a `critique.fix` text
- the graph retries generation
- max retries are bounded by `self_rag_max_retries`

This prevents infinite regeneration loops.

---

## 13. Text2SQL workflow: from user question to safe read-only query

This is a parallel branch of the graph, designed for concrete operational data questions like:
- “How many sev1 incidents in production clusters?”
- “Which services had the most failed deployments?”
- “What were the top 10 SLO breaches last week?”

### 13.1 The schema

`src/advanced_rag/text2sql/database.py` defines the operational data schema:

- `clusters`
- `nodes`
- `deployments`
- `incidents`
- `slo_breaches`

It also populates deterministic seed data:
- fixed random seed for reproducible values
- a sample set of deployment history, incidents, clusters, node status, SLO data

`seed_database()` creates tables, deletes prior rows, inserts generated rows, and returns the total count.

### 13.2 SQL schema prompt

`src/advanced_rag/text2sql/schema.py` inspects the active database using SQLAlchemy:

- `inspect(get_engine()).get_table_names()`
- `get_columns(table)`
- `get_foreign_keys(table)`
- builds a `CREATE TABLE`-like prompt for the model
- includes `COLUMN_NOTES` with semantic hints like:
  - `incidents.severity` = `sev1`, `sev2`, `sev3`
  - `nodes.status` = `Ready`, `NotReady`, `SchedulingDisabled`

The prompt also includes worked examples:
- count sev1 incidents
- failed deployments by service
- average incident duration by root cause

This keeps the model grounded to the actual DB schema instead of guessing.

### 13.3 SQL generation

`src/advanced_rag/text2sql/generator.py`:

- if no LLM is available:
  - returns a `SqlProposal` with an error
- otherwise:
  - builds a prompt with schema + question
  - calls `self.llm.complete_json(...)` using `GeneratedSql`
  - ensures the model used only known tables
  - validates the result with `validate()`

`GeneratedSql` is:
- `sql`
- `rationale`
- `tables`

It retries at most 2 times if validation rejects the draft.

### 13.4 SQL validation

`src/advanced_rag/text2sql/executor.py` implements a strict whitelist gate.

`validate(sql, settings)`:

- strips markdown fences and a trailing semicolon
- requires the query to start with `SELECT` or `WITH ... SELECT`
- rejects any semicolon inside the statement
- rejects forbidden SQL writes using `guardrails.patterns.SQL_WRITE_PATTERNS`
  - `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, etc.
- appends a row limit if missing
- clamps a too-large `LIMIT` to the configured maximum

This is necessary because the repo deliberately denies write operations even if the model proposes them.

### 13.5 Human approval interrupt

`sql_approval_node` in `graph/nodes.py` does:

```python
decision = interrupt({
    "type": "sql_approval",
    "question": state["original_question"],
    "sql": proposal.sql,
    "rationale": proposal.rationale,
    "tables": proposal.tables,
})
```

This is a LangGraph `interrupt()`:
- the run is checkpointed
- the API returns `awaiting_approval=True`
- nothing further touches the DB until `POST /approve`
- resume continues from exactly that step

This is a real human-in-the-loop safety gate.

### 13.6 SQL execution

`sql_execute_node`:

- checks `proposal.approved`
- calls `executor.execute(proposal.sql)`
- wraps execution in a transaction that is rolled back when complete
- sets `sql_rows_text` = a text table, rendered by `render_rows()`

`execute()` does:
- `validate(sql, settings)`
- `engine = get_engine(settings)`
- opens a DB connection
- sets transaction to read-only if Postgres
- runs the query
- fetches up to `sql_row_limit + 1`
- returns `QueryResult(columns, rows, truncated)`

This is safe because it uses:
- read-only DB execution
- transaction rollback
- SQL static validation

The answer generation step then incorporates SQL result rows as context for the final answer.

---

## 14. Finalize and response shaping

`finalize_node` in `graph/nodes.py` does the last state update:

- if the result was blocked, cached, or empty -> do not cache
- if `generation_failed` -> do not cache
- if the route was SQL or BOTH -> do not cache, because live data should not be stale
- otherwise:
  - `get_cache().store(question, payload)`
  - payload includes:
    - answer
    - context
    - route
    - `extractive`
    - citations

Then `graph/pipeline.py::_to_response(...)` builds the API structure:

```python
AnswerResponse(
    question=question,
    answer=answer,
    route=Route(...),
    citations=citations_from(result),
    sql=sql,
    awaiting_approval=awaiting,
    thread_id=thread_id,
    cached=bool(result.get("cached")),
    cache_kind=...,
    extractive=bool(result.get("extractive")),
    guardrails=...,
    blocked=...,
    trace=...,
    input_tokens=...,
    output_tokens=...,
    latency_ms=...
)
```

This is the exact object returned by `/ask` and `/approve`.

---

## 15. UI layer and frontend flow

The frontend lives in `ui/web/` and is served by FastAPI static hosting.

Files:
- `index.html`
- `styles.css`
- `app.js`
- `markdown.js`

The JS app likely:
- calls `/health`
- posts question to `/ask`
- renders answer + citations
- shows pipeline trace
- shows guardrail status
- supports “Without LLM” mode
- allows SQL approval interactions

The actual static mount is in `api/main.py`:
- mount static files at `/`
- custom `NoCacheStaticFiles` adds `Cache-Control: no-cache` so frontend assets never silently go stale

The repo’s UI is intentionally minimal and no-build; it’s a plain browser artifact.

---

## 16. Exact end-to-end path: the call chain in sequence

This is the complete flow, from startup to final answer.

### 16.1 Startup
1. `pyproject.toml` declares scripts
2. `rag-api` command -> `advanced_rag.api.main:run()`
3. `run()` -> `uvicorn.run("advanced_rag.api.main:app", ...)`
4. `api.main` loads settings from `.env`
5. `lifespan(app)` runs:
   - logging setup
   - trust-store enable
   - `pipeline.get_graph()` compiles the LangGraph
6. Web UI static mount is installed

### 16.2 Data ingestion before serving
1. `rag-ingest` CLI starts
2. `main()` loads settings
3. `load_corpus()`
4. `chunk_corpus()`
5. `store.ensure_collection()`
6. `store.upsert(chunks)`
7. Qdrant collection is created and populated
8. If `--seed-sql`, `seed_database()` creates tables and inserts rows into SQLite/Postgres

### 16.3 HTTP request path
1. Browser or client sends `POST /ask`
2. `api.main.ask()` calls `pipeline.ask(question, ...)`
3. `graph.pipeline.ask()` creates `thread_id`, config, and initial state
4. `graph.invoke(initial_state(...), config)`
5. Graph starts at `START -> guardrail_input`

### 16.4 Document-answer path
1. `guardrail_input_node`:
   - shape/pii/secret/injection checks
   - intent/scope LLM checks if configured
2. `cache_lookup_node`:
   - exact and semantic cache lookups
3. `route_node`:
   - route = `vector` / `sql` / `both` / `reject`
4. If route is `vector` or `both`:
   - `retrieve_node()`:
     - HyDE optional
     - Qdrant hybrid retrieval
     - rerank
5. `grade_node()`:
   - CRAG relevance check
6. If verdict weak:
   - `rewrite_node()`
   - loop back to `retrieve`
7. `generate_node()`:
   - extractive or LLM-generated answer
8. `critique_node()`:
   - Self-RAG review
9. `guardrail_output_node()`:
   - output safety review
10. `finalize_node()`:
   - cache answer if allowed
11. `AnswerResponse` returned to caller

### 16.5 SQL-answer path
1. `route_node` returns `sql` or `both`
2. `sql_generate_node` calls `SqlGenerator.generate()`
3. `validate()` rejects non-read-only, non-SELECT queries
4. `sql_approval_node` calls `interrupt()`
5. API returns `awaiting_approval=True`
6. Caller submits `POST /approve`
7. `resume(...)` continues graph
8. `sql_execute_node()` executes the approved SQL in a read-only transaction
9. `generate_node()` consumes the SQL result rows as context
10. Final answer is produced with citations and SQL explanation

---

## 17. Data flow summary: from question to answer

The system is built around a single, explicit flow:

- user question enters the API
- guardrails sanitize or block the request
- cache decides whether to short-circuit
- router chooses between doc retrieval, SQL, or reject
- retrieval accesses Qdrant using hybrid dense/sparse search
- reranking reorders the candidate set
- CRAG grade decides whether the retrieved context is sufficient
- if not, the query is rewritten and retrieval repeats
- answer generation uses the retrieved passages and optionally the SQL output
- Self-RAG critiques the draft
- output guardrails review the answer for safety
- final answer is cached or not based on correctness and mode
- the API returns structured JSON and/or the web UI renders it

The repo therefore does not merely “chat over docs”:
- it routes by intent
- it knows when it needs a database
- it pauses for human approval before executing SQL
- it protects outputs with a nine-layer guardrail pipeline
- it supports offline or model-free operation
- it supports retrieval lab, tuning, and evaluation

---

## 18. The deepest architectural truth

The project is not a simple RAG app; it is a “governed operations assistant” with explicit downstream concerns:

- retrieval quality is treated as a system property, not a side effect
- CRAG loops and HyDE are optional enhancements
- guardrails are sacrificially strict for safety
- SQL is strictly read-only and requires approval
- cache correctness matters more than hit rate
- the repo is designed so that degraded operation is explicit and visible, not silent

This is why so much of the code is about:
- fallback behavior
- safe defaults
- skip semantics
- local model availability
- explicit warnings instead of failure exceptions
- human-in-the-loop database execution

In short: the system is intentionally built to be operationally honest under failure conditions rather than to pretend a model is always available.

---

## 19. Short condensed execution chain

If you want the shortest possible “true execution order,” it is:

1. `.env` loads settings
2. FastAPI app starts
3. graph is compiled
4. `/ask` receives a question
5. guardrails check input
6. cache lookup
7. router decides: vector / sql / both / reject
8. retrieval -> hybrid dense+sparse search -> reranker
9. CRAG grade -> possibly rewrite query -> repeat retrieval
10. generate answer
11. Self-RAG critique
12. output guardrails
13. finalize + cache
14. return JSON response
15. SQL branch pauses at approval and resumes only after `/approve`
16. database rows are rendered into answer context and final response is produced

This is the complete workflow of the repository, start to finish.
```