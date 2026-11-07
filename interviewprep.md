# Interview Prep — Enterprise Advanced RAG (Kubernetes SRE Copilot)

A single-file study guide for walking an interviewer through this project end to end:
the architecture, every folder and the key files/functions in it, the request
lifecycle, the "advanced RAG" techniques and *why* each design decision was made,
plus a Q&A bank and diagrams.

> **One-line pitch:** A production-grade RAG assistant for Kubernetes/SRE operations,
> built on **LangGraph**, that does hybrid retrieval + reranking, four advanced-RAG
> loops (HyDE, CRAG, Self-RAG), natural-language-to-SQL with a human approval gate,
> a 9-layer guardrails pipeline, a two-tier semantic cache, and degrades *honestly*
> to a no-LLM "extractive" mode — all runnable on a laptop with no Docker and no API key.

---

## 1. 30-second and 3-minute pitches

**30 seconds.** "It answers Kubernetes troubleshooting questions. Under the hood it's a
LangGraph state machine: guardrails → cache → a router that decides *documents vs. the
ops database* → hybrid vector retrieval with reranking → a CRAG loop that re-queries if
the context is weak → answer generation → a Self-RAG self-critique → output guardrails.
Database questions take a detour through Text2SQL with a human approval gate before any
query runs. The whole thing runs offline with no model — it just quotes passages instead
of generating prose, and it's honest about that."

**3 minutes.** Add: the retrieval is genuinely hybrid — dense embeddings *and* BM25
sparse vectors in one Qdrant collection, fused either server-side (RRF) or client-side
(weighted). Everything is designed to *degrade gracefully and visibly*: no API key →
extractive answers; `huggingface.co` blocked → pure-Python BM25 fallback; classifier
outage → guardrail layers report `skip`, never a false "pass". The hard-won lessons are
baked in: a failed generation is never cached, citations are stored *with* the cached
answer (a cache hit skips retrieval), and a weak lexical rerank score is allowed to be
*displayed* but never to *drive decisions* (it dropped precision when it did).

---

## 2. Architecture at a glance

```
                              ┌────────────────────────────────────────────┐
   Browser / curl  ──HTTP──►  │  FastAPI app  (api/main.py)                │
                              │  /ask  /approve  /retrieve  /health  /     │
                              │  + static web UI mounted at /              │
                              └───────────────┬────────────────────────────┘
                                              │ pipeline.ask() / resume()
                                              ▼
                              ┌────────────────────────────────────────────┐
                              │  LangGraph state machine (graph/)          │
                              │  nodes = pure fns of RagState → dict patch │
                              └─────────────────┬──────────────────────────┘
          ┌─────────────────────────────────────┼───────────────────────────────────┐
          ▼                                     ▼                                   ▼
  ┌───────────────┐                   ┌───────────────────┐               ┌─────────────────┐
  │ retrieval/    │                   │ text2sql/         │               │ guardrails/     │
  │ Qdrant hybrid │                   │ schema→gen→       │               │ 9 layers        │
  │ + rerank+HyDE │                   │ validate→approve→ │               │ (6 in / 3 out)  │
  └──────┬────────┘                   │ execute (RO)      │               └─────────────────┘
         │                            └─────────┬─────────┘
         ▼                                      ▼
  ┌───────────────┐                   ┌───────────────────┐   ┌──────────┐   ┌──────────┐
  │ Qdrant        │                   │ SQLite / Postgres │   │ llm/     │   │ cache.py │
  │ (embedded/svr)│                   │ ops database      │   │ Claude / │   │ exact +  │
  └───────────────┘                   └───────────────────┘   │ Gemini / │   │ semantic │
                                                              │ offline  │   └──────────┘
                                                              └──────────┘
```

### Pipeline control flow (the graph)

```
guardrail_input ──blocked    ────────────────────────────────────────────────┐
      │ ok                                                                   │
  cache_lookup ──hit ──────────────────────────────────────────────────┐     │
      │ miss                                                           │     │
    route ──reject ──────────────────────────────────────────────┐     │     │
      ├── vector ─► retrieve ─► grade_context ─┬─ correct ──────►│     │     │
      │                 ▲                      │                 │     │     │
      │                 └── rewrite_query ◄────┴─ weak (≤2x)     │     │     │
      │                                                          ▼     │     │
      └── sql/both ─► sql_generate ─► sql_approval ⏸ ─►  sql_execute  │     │ 
                                            │ rejected                 │     │
                                            └──────────────►    generate  ◄──┤
                                                                │    ▲       │
                                                 self_critique ─┘    │ not   │
                                                      │ ok      grounded(≤1x)
                                                      ▼
                                              guardrail_output ─► finalize ─► END
```

`⏸` = a real LangGraph `interrupt()`: the run is checkpointed and **nothing touches the
database** until `POST /approve` resumes that `thread_id`.

---

## 3. Tech stack (and why each piece)

| Concern | Choice | Why |
| --- | --- | --- |
| Orchestration | **LangGraph** `StateGraph` | Explicit state machine with loops, conditional edges, checkpointing + `interrupt()` for human-in-the-loop |
| API + UI | **FastAPI + Uvicorn** | One process serves JSON API *and* the static web UI |
| Vector DB | **Qdrant** (embedded on-disk, or server) | Native **named vectors** (dense + sparse in one point) and server-side RRF fusion |
| Embeddings / rerank | **fastembed** (ONNX, CPU), or **Gemini**, or **pure-Python BM25** | No GPU, no second API key; graceful fallback when HF is blocked |
| LLM | **Anthropic Claude** (default), **Gemini**, or **offline** | Pluggable behind one client interface |
| SQL store | **SQLite** (default) / **PostgreSQL** | Runs with no Docker; Postgres when `POSTGRES_DSN` set |
| Cache | **In-process dict** / **Redis** | Same interface either way |
| Config | **pydantic-settings** (`.env`) | Blank service URL ⇒ local fallback is selected automatically |
| Validation | **Pydantic v2** everywhere | Shared models + structured LLM output via JSON schema |

Python 3.11+. Package lives under `src/advanced_rag/`. Two console scripts in
`pyproject.toml`: `rag-ingest` → `ingestion.cli:main`, `rag-api` → `api.main:run`.

---

## 4. Folder map — what each directory holds

```
src/advanced_rag/
  config.py          Settings (pydantic-settings); blank URLs select local fallbacks
  models.py          shared Pydantic models (Chunk, RetrievedChunk, Route, Verdict, …)
  cache.py           two-tier (exact + semantic) answer cache; Redis or in-process
  observability.py   logging setup, timed() tracer, log_degraded() (dedup noisy fallbacks)
  certs.py           truststore.inject_into_ssl() for corporate TLS-inspecting proxies
  llm/
    client.py        Anthropic wrapper: complete(), complete_json(), llm_available()
    gemini_client.py Gemini backend (same interface)
    prompts.py       FROZEN system prompts (module constants ⇒ prompt cache stays warm)
    extractive.py    no-LLM answer: quote top passages, say "this is extractive"
  ingestion/
    loader.py        parse front matter, load_corpus(), chunk_corpus()
    chunker.py       markdown-aware: split_sections(), window(), chunk_document()
    cli.py           `rag-ingest` entry point (--recreate/--dry-run/--seed-sql)
  retrieval/
    embeddings.py    Embedder / KeywordEmbedder / GeminiEmbedder; Reranker variants
    bm25.py          pure-Python BM25 (Porter2 stemmer) + IDF-weighted rerank fallback
    vectorstore.py   HybridStore: ensure_collection/upsert/search, RRF + weighted fusion
    retriever.py     Retriever: HyDE → hybrid search → rerank; format_context()
  text2sql/
    database.py      DDL, deterministic seed data, get_engine(), QueryResult
    schema.py        live schema introspection → prompt (with COLUMN_NOTES + examples)
    generator.py     SqlGenerator.generate(): LLM → validate → retry (≤2)
    executor.py      validate() static gate + execute() read-only rolled-back txn
  guardrails/
    patterns.py      regex sets: SECRET, PII, SECRET_REQUEST, INJECTION, DESTRUCTIVE, SQL_WRITE
    pipeline.py      Guardrails.check_input()/check_output(); 9 layers
  graph/
    state.py         RagState TypedDict + initial_state(); trace uses operator.add reducer
    nodes.py         every node: guardrail_input, cache_lookup, route, retrieve, grade,
                     rewrite, generate, critique, sql_generate/approval/execute, finalize
    builder.py       build_graph(): wires nodes/edges FROM FEATURE FLAGS; checkpointer
    pipeline.py      public ask() / resume() / pending_approval(); _to_response()
  evaluation/
    dataset.py       hand-written golden set
    retrieval_metrics.py   hit@k, recall, MRR, NDCG, precision@k
    runner.py        `python -m advanced_rag.evaluation.runner --retrieval/--guardrails/--answers`
  api/main.py        FastAPI app, routes, static UI mount (NoCacheStaticFiles)
data/corpus/         8 k8s runbooks + a postmortem + a policy doc (markdown)
ui/web/              index.html, styles.css, app.js, markdown.js (no build step)
tests/               offline suite (no API key needed)
```

---

## 5. End-to-end walkthrough (the call chain)

### 5.1 Startup
1. `rag-api` → `api.main:run()` → `uvicorn.run("advanced_rag.api.main:app", …)`.
2. `lifespan(app)` runs once: `get_settings()`, `setup_logging()`,
   `enable_system_trust_store()` (certs.py), then `pipeline.get_graph()` to **compile
   the LangGraph up front** so the first request doesn't pay compile cost.
3. `_web_dir()` locates the UI (wheel path `api/web`, else source `ui/web`) and mounts it
   at `/` via `_NoCacheStaticFiles` (forces ETag revalidation so a stale `app.js` never
   runs against a new `index.html`). Routes declared above the mount keep priority.

### 5.2 Ingestion (`rag-ingest`, must run before serving)
`ingestion/cli.py::main()` →
`load_corpus()` (loader.py, reads `data/corpus/*.md`, parses front matter) →
`chunk_corpus()` → `chunker.chunk_document()` (split on headings, sliding sentence window
only for oversized sections; **content-addressed chunk IDs** so re-ingest overwrites in
place) → `HybridStore.ensure_collection()` (dense+sparse, or sparse-only if no dense arm)
→ `HybridStore.upsert()` (embeds each batch, builds `PointStruct`s). With `--seed-sql`,
`text2sql.database.seed_database()` creates tables and inserts deterministic rows
(fixed RNG seed `20260828` so the eval set can assert on exact counts).
Reported result: **8 docs → 49 chunks**, **416 ops rows**.

### 5.3 Request lifecycle: `POST /ask`
`api.main.ask()` → `pipeline.ask(question, thread_id, force_extractive=not use_llm)` →
makes a `thread_id`, builds config (`recursion_limit=40`), `graph.invoke(initial_state(…))`
→ walks the graph (below) → `_to_response()` shapes an `AnswerResponse`.

**Node by node (document path):**
1. **`guardrail_input_node`** → `Guardrails.check_input()` runs layers 1–6.
2. **`cache_lookup_node`** → `AnswerCache.lookup()` (exact then semantic). A hit
   short-circuits straight to `finalize`. *Mode guard:* a cached answer whose
   `extractive` flag ≠ this run's mode is **not** served.
3. **`route_node`** → `vector` / `sql` / `both` / `reject` via `complete_json(RouteDecision)`.
   No LLM or text2sql disabled ⇒ forced to `vector` (safe default, no credentials).
4. **`retrieve_node`** → `Retriever.retrieve()`: optional HyDE → `HybridStore.search()`
   → `rerank()`. On a corrective pass it uses `retrieve_multi(rewrites)`.
5. **`grade_node`** (CRAG) → cheap cross-encoder floor first, then LLM grader
   (`ContextGrade`); verdict `correct`/`ambiguous`/`incorrect`.
6. **`rewrite_node`** (only if weak) → `rewrite_query()` proposes 3 queries → loops back
   to `retrieve` (bounded at **2** attempts by `MAX_RETRIEVAL_ATTEMPTS`).
7. **`generate_node`** → `complete(ANSWER_SYSTEM)` or `extractive_answer()` offline.
8. **`critique_node`** (Self-RAG) → `complete_json(Critique)`; if not acceptable, returns
   a fix and loops back to `generate` (bounded by `self_rag_max_retries`, default 1).
9. **`guardrail_output_node`** → layers 7–9 (skipped if generation failed/blocked —
   nothing model-authored to review).
10. **`finalize_node`** → caches the answer **unless** blocked / cached / empty /
    `generation_failed` / route is SQL/BOTH.

**SQL path:** `route` → `sql_generate_node` → `sql_approval_node` (`interrupt()`, returns
`awaiting_approval=True`) → client `POST /approve` → `pipeline.resume()` with
`Command(resume={"approved": …})` → `sql_execute_node` (read-only, rolled-back) → the
rows become context for `generate_node`.

---

## 6. The advanced-RAG techniques — deep dives with the "why"

Each is gated by a feature flag in `config.py`. **Turning a flag off removes the node from
the compiled graph** (`builder.py`), not just no-ops it — so you can reconstruct the
baseline RAG pipeline and measure what each layer buys.

### 6.1 Hybrid retrieval (`retrieval/vectorstore.py`)
- **What:** each Qdrant point carries two **named vectors** — `dense` (semantic embedding)
  and `sparse` (BM25). `search(mode="hybrid")` fuses them.
- **Two fusion modes:** `_hybrid_rrf()` = server-side Reciprocal Rank Fusion (one round
  trip, no tunable weight); `_hybrid_weighted()` = client-side, min-max normalizes each
  arm and combines with `HYBRID_DENSE_WEIGHT` (default 0.7).
- **Why weighted is the default:** measured **p@5 0.76 weighted vs 0.71 RRF** on the
  golden set. The dial matters because dense wins on *paraphrased* questions and sparse
  wins on *exact error strings / resource names* (`exit code 137`, `imagefs.available`) —
  most of k8s troubleshooting.
- **Graceful degrade:** no dense arm ⇒ `mode="hybrid"` silently serves sparse-only rather
  than erroring.

### 6.2 BM25 fallback (`retrieval/bm25.py`)
- **Why it exists:** `huggingface.co` is blocked on the corp network, so *even BM25's
  tokeniser assets* can't download. This is a pure-Python reimplementation (Porter2 stemmer
  via `snowballstemmer`, FNV-1a token hashing so IDs survive process restarts).
- **Scoring split:** the document vector carries the TF component; Qdrant's **IDF modifier**
  on the sparse vector supplies the corpus half — exactly how `Qdrant/bm25` is wired.
- **Talking point:** "It's a real BM25, not a toy. What's genuinely lost offline is
  *dense/semantic recall* and a *true cross-encoder*, not lexical matching."

### 6.3 Reranking (`retrieval/retriever.py::rerank`, `embeddings.py`)
- Cross-encoder scores `(query, passage)` **jointly**; squashed through `sigmoid()` to 0–1
  so the CRAG floor is meaningful.
- **The key lesson** (great interview story): the **lexical fallback reranker may score but
  must not reorder.** Letting it reorder dropped p@5 from 0.76 → 0.64. So
  `RetrievedChunk.rerank_is_authoritative` tracks whether a *real* cross-encoder produced
  the score; only authoritative scores drive ordering and the CRAG floor.
- `RetrievedChunk.score` returns the rerank score only when authoritative, else the
  retrieval score. The `/retrieve` API returns `rerank_is_authoritative` + `ordered_by`
  so a client can label its own columns honestly.

### 6.4 HyDE — Hypothetical Document Embeddings (`retriever.generate_hyde`)
- **What:** ask the fast model to draft a hypothetical *answer* passage, embed
  `question + "\n\n" + hyde_doc`, and search with that.
- **Why:** questions and runbooks are written differently; a fake answer closes the
  surface-form gap. The *real question* is kept in the query so lexical matching still has
  the user's exact error strings.
- **Rerank against the real question**, not the HyDE passage (scoring against a guess would
  reward documents similar to the guess). HyDE is an optimisation — it fails open.

### 6.5 CRAG — Corrective RAG (`grade_node`, `rewrite_node`)
- **What:** grade the retrieved context *before* spending a generation on it. If weak,
  rewrite the query and retrieve again (≤2 passes).
- **Two-stage grading (cost-aware):** cheap cross-encoder floor first
  (`CRAG_RELEVANCE_FLOOR=0.25`); only if it passes do we pay for an LLM grader call.
- **The subtle bug fixed:** gating on the *lexical* score was actively harmful — four of
  four correctly-retrieved semantic queries scored 0.005–0.133 and tripped the floor,
  burning a rewrite and telling `generate` to hedge about *correct* context. Fix: the
  floor only consults **authoritative** scores.

### 6.6 Self-RAG (`critique_node`)
- **What:** a reviewer grades the draft on three axes — `grounded`, `addresses_question`,
  `cited`. If any fails, it returns a `fix` and regeneration runs again with the critique
  injected (bounded by `self_rag_max_retries`).
- **Offline:** nothing to critique — an extractive answer is quoted source text, so there's
  no paraphrase that could have drifted.

### 6.7 Text2SQL with human approval (`text2sql/`)
- **Schema is introspected live** (`schema.py`), never hardcoded — the prompt can't drift
  from the real tables. It includes `COLUMN_NOTES` (enum values, units) and worked examples.
- **Generate → validate → retry** (`generator.py`, ≤2 attempts): re-prompts with the
  validator's complaint; rejects unknown tables.
- **Two independent safety gates** (`executor.py`): (1) `validate()` — static: must start
  `SELECT`/`WITH…SELECT`, no stacked statements (no inner `;`), rejects
  `SQL_WRITE_PATTERNS`, enforces/clamps `LIMIT`. (2) **Human approval** via `interrupt()`.
  Then `execute()` runs inside a **rolled-back, read-only transaction** (belt-and-braces:
  even a validator bug can't mutate anything; Postgres also sets `transaction_read_only`
  and a `statement_timeout`).
- **Why never cache SQL answers:** they depend on live data; a cached row count would go
  stale under an identical-looking question.

### 6.8 Guardrails — 9 layers (`guardrails/pipeline.py`)

| # | Layer | Dir | Trip action | Kind |
| --- | --- | --- | --- | --- |
| 1 | `shape` | in | block (empty / >4000 chars) | regex |
| 2 | `pii_redaction` | in | redact, continue | regex |
| 3 | `secret_request` | in | block | regex |
| 4 | `injection` | in | block | regex |
| 5 | `intent` | in | block | **LLM** |
| 6 | `scope` | in | block | **LLM** |
| 7 | `destructive_output` | out | annotate w/ change-control notice | regex |
| 8 | `secret_egress` | out | redact | regex |
| 9 | `output_review` | out | block (grounding + safety) | **LLM** |

- **Ordered by cost:** 6 deterministic (microseconds) run before any token is spent; the 3
  LLM layers only ever see input that survived them. Layers 5 & 6 run **concurrently** in a
  thread pool (they're independent).
- **Failing open is reported, not hidden** — the single best design point to cite. An LLM
  layer that can't run returns `action="skip"`, `passed=False`; the UI renders
  *"6 of 9 layers ran, 3 SKIPPED"* with a warning, never a row of green ticks.
  `passed` means *ran AND cleared*. The skip reason distinguishes `offline mode` from a
  `classifier unavailable` outage — a reader acts differently on each.

### 6.9 Two-tier cache (`cache.py`)
- **Exact tier:** `sha256` of the normalized question. **Semantic tier:** embeds the
  question, cosine-similarity lookup above `SEMANTIC_CACHE_THRESHOLD=0.95`.
- **Two correctness rules (both learned by running the service):**
  1. **A failed generation is never cached** — else one network blip is replayed as the
     answer for the whole TTL. (`generation_failed` flag → `finalize_node` refuses.)
  2. **Citations are stored *with* the answer** — a cache hit skips retrieval, so `[1]`
     markers would otherwise resolve to an empty sources panel.
- Semantic tier **latches off** after one embedder failure (one warning, not one traceback
  per request); the exact tier is unaffected.

---

## 7. Graceful degradation — the project's spine (big talking point)

| Failure | What happens | Where |
| --- | --- | --- |
| No API key / `LLM_PROVIDER=offline` | Extractive answers (quoted passages, `extractive:true`), SQL routing disabled, HyDE/CRAG-rewrite no-op; guardrails 5/6/9 report `skip` | `llm_available()` latches once; `extractive.py` |
| `huggingface.co` blocked | Dense falls back to GCS mirror or keyword BM25; sparse → local BM25; reranker → lexical | `embeddings.get_embedder()` |
| Corp TLS proxy (`CERTIFICATE_VERIFY_FAILED`) | `truststore.inject_into_ssl()` uses OS trust store | `certs.py` |
| Classifier outage | guardrail layer `skip`, not pass | `guardrails/pipeline.py::_classify` |
| LLM generation error | honest fallback message + citations, **not cached** | `generate_node` |
| Embedded Qdrant locked | `EmbeddedStoreBusyError` names both ways out | `vectorstore.py` |
| Postgres checkpointer missing | falls back to `MemorySaver` with a warning | `builder._make_checkpointer` |

Principle: **"operationally honest under failure rather than pretending the model is always
available."**

---

## 8. Likely interview questions & crisp answers

**Q: Why LangGraph instead of a plain function chain / LangChain?**
A: The pipeline has *loops* (CRAG re-query, Self-RAG regenerate), *conditional branching*
(router), and a *pause-and-resume* (SQL approval). LangGraph gives a typed `StateGraph`,
conditional edges, bounded recursion, checkpointing, and a first-class `interrupt()` for
human-in-the-loop. Nodes are pure `state → patch` functions, so each is unit-testable in
isolation and the builder rewires the graph from feature flags.

**Q: How does state flow between nodes?**
A: `RagState` is a `TypedDict` (not Pydantic) because LangGraph merges each node's returned
dict into it. `trace` and `guardrails` use an `operator.add` reducer so every node *appends*
its step without reading prior nodes. `original_question` is preserved separately from
`question` (which guardrail redaction / CRAG rewriting mutate) for citations and cache keys.

**Q: What makes the retrieval "hybrid" and why bother?**
A: Dense + sparse named vectors in one collection. Measured p@5: dense 0.65, sparse 0.68,
hybrid-weighted 0.76 — the arms find genuinely different passages. Dense captures paraphrase;
sparse nails exact error strings and resource names.

**Q: RRF vs weighted fusion?**
A: RRF is server-side, one round trip, weight-free, robust. Weighted is client-side, costs a
second query per arm, but exposes `HYBRID_DENSE_WEIGHT` as a real dial and beat RRF 0.76 vs
0.71 here. Weighted is the default; RRF is available via the API.

**Q: Your rerank score is low but retrieval was right — what's going on?**
A: That's the lexical *fallback* scorer, which can't see word order or meaning. It's the
reason `rerank_is_authoritative` exists: a weak score may be *shown* but never *drives*
ordering or the CRAG floor. Letting it reorder dropped p@5 to 0.64; gating CRAG on it
rejected correct context. Only a real cross-encoder (or the Gemini LLM-reranker) is trusted.

**Q: How do you stop the LLM from running a destructive SQL query?**
A: Three independent defenses. Static `validate()` allows only a single read-only
SELECT/CTE, rejects write patterns and stacked statements, enforces a LIMIT. Then a human
approval `interrupt()`. Then execution inside a rolled-back, read-only transaction with a
server-side timeout. Writes can't happen even if two of the three fail.

**Q: How does the approval gate actually pause a web request?**
A: `sql_approval_node` calls LangGraph `interrupt()`, which checkpoints state and raises out
of the run. `/ask` returns `awaiting_approval=True` + the proposed SQL + `thread_id`.
`/approve` calls `resume()` with `Command(resume={"approved": bool})`, which continues from
exactly that node — no earlier work repeats. (Caveat: default `MemorySaver` is process-local,
so `/approve` must hit the same replica; `CHECKPOINT_BACKEND=postgres` fixes that.)

**Q: What's the hardest correctness bug you hit?**
A: Caching. A transient LLM outage got cached as "the answer" and replayed for the whole
TTL — fixed with a `generation_failed` flag that blocks caching. And cache hits came back
with empty citation panels because a hit skips retrieval — fixed by storing citations *with*
the answer. Also the mode guard: a "Without LLM" request must never receive a cached
*generated* answer, so the cache keys carry the `extractive` flag.

**Q: How is "no API key" not just a broken state?**
A: `llm_available()` probes the client once and latches. Offline, retrieval still does the
useful half (find + cite passages); `extractive_answer()` quotes the top 4 and *says* it's
extractive. The response carries `extractive:true`; the UI tags it. It's a different product,
surfaced as such — not a silent downgrade.

**Q: How do you keep prompt-cache hits?**
A: System prompts in `llm/prompts.py` are module-level constants sent with
`cache_control:ephemeral`. Never interpolate per-request values into them or the cached
prefix breaks. Even the SQL schema prompt resolves `today's date` once per process for the
same reason.

**Q: How do graders stay cheap?**
A: Router, CRAG grader, Self-RAG critic, and guardrail classifiers run on `LLM_FAST_MODEL`
(Haiku) at `effort=low` with JSON-schema structured output (`complete_json`). Only final
answer generation uses the full model at `LLM_EFFORT=high`. `client.py` checks per-model
capability (`supports_effort`, `supports_fallbacks`) so sending `effort` to an older model
isn't a 400.

**Q: How do you know any of this works? (evaluation)**
A: `evaluation/runner.py --retrieval` scores six strategies on hit@k, recall, MRR, NDCG,
p@5 against a hand-written golden set — deterministic and nearly free, the place to tune.
Honest caveat it prints itself: hit@5/recall/MRR are pegged at 1.00 (golden set saturated),
so only p@5 discriminates. Plus `--guardrails` (block/allow accuracy), `--answers`
(end-to-end, optional Ragas), and a 160-test offline `pytest` suite.

**Q: Known gaps / what would you do next?**
A: `MemorySaver` loses approval state on restart and across workers → swap
`langgraph-checkpoint-postgres`. Embedded Qdrant takes an exclusive lock (can't ingest +
serve at once) → run the container. `pending_approval()` returns only the first interrupt.
Payload indexes are a no-op in embedded Qdrant. Corpus is a small hand-written sample. Ragas
re-retrieves context rather than reusing what the run saw.

---

## 9. Numbers worth memorizing

- Corpus: **8 docs → 49 chunks** (median ~415 chars). Ops DB: **416 rows**, RNG seed `20260828`.
- Retrieval p@5: dense **0.65**, sparse **0.68**, hybrid-RRF **0.71**, **hybrid-weighted 0.76**.
  Lexical reorder regression: **0.76 → 0.64**.
- Defaults: `RETRIEVE_TOP_K=20`, `RERANK_TOP_N=5`, `HYBRID_DENSE_WEIGHT=0.7`,
  `CRAG_RELEVANCE_FLOOR=0.25`, `SEMANTIC_CACHE_THRESHOLD=0.95`, CRAG ≤2 passes,
  Self-RAG ≤1 retry, recursion limit 40.
- Worst-case run ≈ **8 model calls** (route, HyDE, grade, rewrite, generate ×2, critique, +
  guardrail classifiers) — cache + rerank floor keep the common case well below that.
- Guardrails: **9 layers**, 6 in / 3 out, 6 deterministic + 3 LLM.

---

## 10. 60-second whiteboard script

"Request hits FastAPI, which calls `pipeline.ask()` into a LangGraph state machine. First
**guardrails** — six cheap inbound checks, two of them LLM classifiers that *fail open and
say so*. Then the **cache** — exact then semantic; a hit short-circuits the whole pipeline.
Then a **router** picks documents, the ops database, or both. The document path does
**hybrid retrieval** — dense embeddings plus BM25 in Qdrant, fused by weighted score — then
**reranks**, then **CRAG** grades the context and re-queries up to twice if it's weak. The
answer is generated, **Self-RAG** critiques and maybe regenerates, output guardrails run, and
we cache — but never a failed generation and never live SQL. The database path generates a
read-only query, **pauses for human approval via a real interrupt**, then runs it in a
rolled-back transaction. And the whole thing runs with no model at all — it quotes passages
and tells you it's doing that."

---

*Generated as a study aid; cross-check line numbers against the source, which is the ground
truth. Key files to re-read the night before: `graph/builder.py`, `graph/nodes.py`,
`retrieval/retriever.py`, `guardrails/pipeline.py`, `text2sql/executor.py`, `cache.py`.*
