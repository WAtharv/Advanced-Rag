# Enterprise Advanced RAG in LangGraph

A production-grade Enterprise RAG system for Kubernetes IT operations, built with
LangGraph, FastAPI, Qdrant, PostgreSQL and Redis — covering hybrid search,
reranking, HyDE, CRAG, Self-RAG, Text2SQL with human approval, caching and a
nine-layer guardrails pipeline.

### Free-First AI Architecture

The system defaults to a **Free-First AI architecture**:
- **Hosted LLM (Default):** [Groq](https://console.groq.com) free tier (`llama-3.3-70b-versatile` & `llama-3.1-8b-instant`) via the official Groq SDK for ultra-fast LPU inference.
- **Local LLM (Optional):** [Ollama](https://ollama.ai) (`llama3.2`) for 100% private, on-premise execution with zero cloud dependencies.
- **Local Embeddings (Default):** [FastEmbed](https://github.com/qdrant/fastembed) on CPU using `BAAI/bge-small-en-v1.5` (384-dimensional dense vectors) combined with pure-Python BM25 sparse search.
- **Local Reranking (Default):** FastEmbed cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` with automatic graceful fallback to lexical scoring if unavailable.
- **Offline Extractive Fallback:** Deterministic quoting of runbooks with pure BM25 search and regex guardrails when no LLM is configured.
- **Legacy Cloud Providers (Optional):** Claude (`anthropic`) and Google Gemini (`gemini`) remain supported when explicitly configured.

> `todo.md` holds the project structure and roadmap and is maintained by hand —
> this README documents what is implemented and how to run it.

## What is implemented

| Layer | Where | Notes |
| --- | --- | --- |
| LLM Provider: Groq (Default) | `llm/groq_client.py` | Official Groq SDK, fast structured JSON, rate-limit backoff, fallback models |
| LLM Provider: Ollama | `llm/ollama_client.py` | Local HTTP API, health check, structured JSON, 100% private |
| Markdown-aware chunking | `ingestion/chunker.py` | Splits on headings first, sliding sentence window only for oversized sections |
| Hybrid search | `retrieval/vectorstore.py` | Dense (384d BGE-Small) + BM25 named vectors in Qdrant; server-side RRF **or** client-side weighted fusion |
| Pure-Python BM25 | `retrieval/bm25.py` | Pure-Python BM25 + Porter2 stemmer, no model download; standalone or hybrid |
| Cross-encoder reranking | `retrieval/embeddings.py` | FastEmbed `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder with calibrated sigmoid logits and lexical fallback |
| Keyword-only fallback | `retrieval/bm25.py` | Pure-Python BM25 + Porter2 stemmer, no model download; `RETRIEVAL_BACKEND=keyword`. Its stand-in reranker uses IDF-weighted coverage over the candidate set — plain coverage quantises to k/n and ties everything |
| Cross-encoder reranking | `retrieval/embeddings.py` | Scores are squashed to 0–1 so the CRAG floor is stable. When only the lexical stand-in is available it **scores but does not reorder** — measured, reordering by it dropped p@5 from 0.76 to 0.64 |
| HyDE | `retrieval/retriever.py` | Hypothetical passage is embedded; reranking still scores against the real question |
| CRAG | `graph/nodes.py` · `grade_node` | Cheap rerank-score floor first, grader call only if it passes; corrective query rewrite, bounded at 2 passes |
| Self-RAG | `graph/nodes.py` · `critique_node` | Grades grounded / addresses-question / cited, then regenerates with the critique |
| Text2SQL | `text2sql/` | Schema introspected live, validated read-only SELECT, human approval gate, rolled-back transaction |
| Semantic cache | `cache.py` | Exact key tier + embedding-similarity tier; Redis or in-process |
| Guardrails | `guardrails/` | 9 layers, 6 inbound and 3 outbound (see below) |
| Evaluation | `evaluation/` | Deterministic retrieval metrics, guardrail accuracy, end-to-end answers, optional Ragas |
| API | `api/main.py` | `/ask`, `/approve`, `/retrieve`, `/health`, `/documents` |
| UI | `ui/web/` | Chat, pipeline trace, approval gate, retrieval lab. Styled after USWDS v3 (light mode, tokens copied by value); plain HTML/CSS/JS with no build step, mounted at `/` by the API itself |

### The nine guardrail layers

| # | Layer | Direction | Action on trip |
| --- | --- | --- | --- |
| 1 | `shape` | in | block (empty or oversized) |
| 2 | `pii_redaction` | in | redact, then continue |
| 3 | `secret_request` | in | block |
| 4 | `injection` | in | block |
| 5 | `intent` | in | block (LLM classifier) |
| 6 | `scope` | in | block (LLM classifier) |
| 7 | `destructive_output` | out | annotate with the change-control requirement |
| 8 | `secret_egress` | out | redact |
| 9 | `output_review` | out | block (LLM grounding + safety review) |

Layers 1–4 and 7–8 are deterministic and run in microseconds; the LLM-backed
layers only ever see input that survived them.

**Failing open is reported, not hidden.** Layers 5, 6 and 9 need the model, and a
classifier outage degrades safety rather than availability — the right trade for
an on-call tool, but only if the reader knows it happened. Such a layer returns
`action="skip"` with `passed=False`, and the UI renders `"6 of 9 layers ran,
3 SKIPPED"` with a warning rather than a row of green ticks. `passed` means the
layer ran *and* cleared the content; a layer that vetted nothing never counts as
a pass.

## Requirements

- Python 3.11+
- An API key is **optional** — see [Running Free-First](#running-the-free-first-ai-architecture).
  - Default: [Groq free tier](https://console.groq.com) (`GROQ_API_KEY`).
  - 100% Local: [Ollama](https://ollama.ai) (`LLM_PROVIDER=ollama`).
  - Deterministic Offline: Quoted runbook citations (`LLM_PROVIDER=offline`).
- Docker is **optional** — embedded Qdrant and SQLite run locally with zero containers.

## Running the Free-First AI Architecture

### Option A — Hosted Free Tier (Recommended Default)
Uses Groq's high-speed free tier for inference and FastEmbed on local CPU for embeddings/reranking:
```bash
cp .env.example .env        # add free GROQ_API_KEY
rag-ingest --seed-sql       # index corpus into 384d Qdrant collection + create ops database
rag-api                     # UI at http://127.0.0.1:8000
```

### Option B — 100% Local AI (No Cloud Keys, Private)
Uses Ollama running on your local machine:
```bash
ollama run llama3.2         # pull and run local model
LLM_PROVIDER=ollama rag-api
```

### Option C — Fully Offline Mode (Deterministic Extractive)
Runs entirely without a language model; quotes verified runbooks verbatim:
```bash
LLM_PROVIDER=offline rag-api   # cited passages instead of generated prose
```

## Running without an API key

Set `LLM_PROVIDER=offline`, or just leave the provider's key blank — it is
detected automatically. The probe runs once per process and latches, so a
missing key costs one warning rather than a traceback per node per request.

What still works, because none of it needs a model:

| Works offline | Notes |
| --- | --- |
| Retrieval | BM25 + Porter2 with `RETRIEVAL_BACKEND=keyword`: no download, no key |
| Reranking | The lexical stand-in scorer; it scores but does not reorder |
| Citations | Derived from the retrieved chunks, so `[n]` markers still resolve |
| Guardrail layers 1–4, 7–8 | Deterministic regex; `injection` and `secret_request` still block |
| Exact answer cache | The semantic tier needs an embedder and disables itself with one warning |
| Retrieval lab | Its whole point is comparing strategies without generation |

What changes:

- **Answers become extractive.** `generate_node` quotes the top four passages
  under their section headings instead of synthesising, and says so in the first
  line of every answer. The response carries `extractive: true` and the UI shows
  an `extractive · no LLM` tag plus a standing notice in the sidebar. An
  extractive answer *is* cached — it is a pure function of the retrieved chunks,
  so unlike a failed generation it is safe to remember.
- **Text2SQL is unavailable.** Classifying a question as SQL-shaped needs a
  model, so the router sends everything to vector retrieval. Ask "how many sev1
  incidents" offline and you get runbook passages, not a row count.
- **Guardrail layers 5, 6 and 9 report `skip`** with the detail
  `no LLM configured (offline mode)` — distinguishable from a classifier outage,
  which reads `classifier unavailable`. The UI still renders
  `6 of 9 layers ran, 3 SKIPPED`; failing open is never presented as a pass.
- **HyDE and CRAG query rewriting no-op**, so recall depends more on using the
  vocabulary the runbooks use.

### If the model download fails on a corporate network

Two distinct problems, in the order you will hit them:

1. **`CERTIFICATE_VERIFY_FAILED`** — a TLS-inspecting proxy's CA is not in the
   certifi bundle. The entry points call `truststore.inject_into_ssl()` so
   verification uses the OS trust store instead, where the corporate CA already
   lives. Set `RAG_DISABLE_TRUSTSTORE=1` to opt out and manage `SSL_CERT_FILE`
   yourself.
2. **`Could not load model ... from any source`** — `huggingface.co` is blocked by
   the web filter. fastembed has no alternate source for these models, so there
   are three ways out:
   - have `huggingface.co` allowlisted for the machine;
   - set `HF_ENDPOINT` to an internal Hugging Face mirror;
   - copy a populated fastembed cache onto the machine and set `MODEL_CACHE_DIR`
     to it in `.env`.

   **Before any of that, check the GCS mirror.** Six of fastembed's *dense* models
   are also hosted on `storage.googleapis.com`, which many filters allow even when
   they block `huggingface.co`. fastembed tries Hugging Face first, logs the
   failure, and falls back to the mirror automatically — so on such a network you
   get real dense embeddings just by naming one of these in `DENSE_MODEL`:

   | Model | Dim | Size |
   | --- | --- | --- |
   | `BAAI/bge-base-en-v1.5` | 768 | 204 MB |
   | `BAAI/bge-small-en` | 384 | 78 MB |
   | `sentence-transformers/all-MiniLM-L6-v2` | 384 | 83 MB |
   | `BAAI/bge-base-en` | 768 | 420 MB |
   | `BAAI/bge-small-zh-v1.5` | 512 | 90 MB |
   | `intfloat/multilingual-e5-large` | 1024 | 2.24 GB |

   Every fastembed **sparse** model and **reranker** is HF-only, with no mirror. So
   the two arms fall back independently: dense from GCS, sparse from the local
   BM25 (`SPARSE_MODEL=local-bm25` skips the pointless probe — fastembed retries a
   blocked download three times with backoff, ~40 s per process start), and
   reranking stays lexical.

   **An `HF_TOKEN` does not help here.** Measured on this network: `/api/whoami-v2`,
   the model metadata endpoint and the file endpoint all return the same 403 HTML
   block page with and without a token. `whoami-v2` is a pure auth endpoint — a
   valid token returns JSON and an invalid one returns a JSON 401 — so an HTML
   response proves the request is being terminated before it reaches Hugging
   Face. `HF_TOKEN` is still read from `.env` and exported for `huggingface_hub`,
   for use on a network that permits the connection.

`RETRIEVAL_BACKEND` decides what happens in the meantime:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Try fastembed once at startup; fall back to keyword BM25 with a warning if the models cannot be fetched |
| `fastembed` | Dense + sparse + cross-encoder. Fails loudly if the models are missing |
| `keyword` | Pure-Python BM25 only (`retrieval/bm25.py`) — no download. Sparse-only collection, lexical-overlap stand-in for the reranker, `mode="hybrid"` degrades to sparse |

The keyword backend is a real BM25: documents carry the term-frequency component
and Qdrant's IDF modifier on the sparse vector supplies the corpus half, which is
exactly how `Qdrant/bm25` is wired — that model is a tokeniser and a hash, not a
neural net. What is genuinely lost is dense/semantic recall and true
cross-encoder reranking.

Verify without spending tokens:

```bash
rag-ingest --dry-run                                   # chunking only
python -m advanced_rag.evaluation.runner --retrieval   # retrieval metrics
pytest -q                                              # offline test suite (160 tests)
```

Note: python startup on a machine with aggressive AV scanning can be ~8 s, and
the first import of `qdrant-client` several minutes. The suite itself takes
40 s–5 min depending on cache warmth.

## With Docker

```bash
docker compose up -d          # qdrant, postgres, redis
```

Then point `.env` at them and re-ingest:

```dotenv
QDRANT_URL=http://localhost:6333
POSTGRES_DSN=postgresql+psycopg://postgres:postgres@localhost:5432/ops
REDIS_URL=redis://localhost:6379/0
```

```bash
rag-ingest --recreate --seed-sql
```

## Pipeline

```
guardrail_input ──blocked   ─────────────────────────────────────────┐
       │                                                             │
   cache_lookup ──hit  ───────────────────────────────────────────┐  │
       │                                                          │  │
     route ──reject───────────────────────────────────────────┐   │  │
       ├── vector ──► retrieve ──► grade_context ─┬─correct──►│   │  │
       │                 ▲                        │           │   │  │
       │                 └──── rewrite_query ◄────┴─weak      │   │  │
       │                                                      │   │  │
       └── sql/both ─► sql_generate ─► sql_approval ⏸ ─► sql_execute
                                                              │
                              generate ◄────────────────────────
                                 │  ▲
                          self_critique ──not grounded──┘
                                 │
                        guardrail_output ──► finalize ──► END
```

`⏸` is a real LangGraph `interrupt()`: the run is checkpointed and nothing
touches the database until `POST /approve` resumes that `thread_id`.

## API

```bash
# Documentation question
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"Why did my pod exit with code 137?"}' | jq '.answer, .citations'

# Database question — comes back awaiting approval
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"How many sev1 incidents since June 2026?"}' | jq '.awaiting_approval, .sql.sql, .thread_id'

curl -s localhost:8000/approve -H 'content-type: application/json' \
  -d '{"thread_id":"<thread_id>","approved":true}' | jq '.answer'

# Retrieval only, for tuning
curl -s localhost:8000/retrieve -H 'content-type: application/json' \
  -d '{"query":"exit code 137","mode":"hybrid","fusion":"rrf","rerank":true}' | jq '.results[0]'

# Document Management (Phase C)
# 1. Upload a document (supports .md, .pdf, .docx, .txt, .csv, .html, .json up to 20MB)
curl -s -X POST http://127.0.0.1:8000/documents/upload \
  -F "file=@incident-runbook.md" | jq

# 2. List all indexed documents (built-in corpus + dynamic uploads)
curl -s http://127.0.0.1:8000/documents | jq

# 3. Replace an existing uploaded document using its document_id
curl -s -X POST http://127.0.0.1:8000/documents/upload \
  -F "file=@incident-runbook-v2.md" \
  -F "document_id=<document_id>" | jq

# 4. Delete an uploaded document (built-in corpus documents are protected)
curl -s -X DELETE http://127.0.0.1:8000/documents/<document_id> | jq

# Interactive OpenAPI docs (Swagger UI) available at:
# http://127.0.0.1:8000/docs
```

## Evaluation

```bash
python -m advanced_rag.evaluation.runner --retrieval    # cheap, deterministic
python -m advanced_rag.evaluation.runner --guardrails   # block/allow accuracy
python -m advanced_rag.evaluation.runner --answers --limit 5
python -m advanced_rag.evaluation.runner --answers --ragas
```

`--retrieval` scores six strategies (dense, sparse, hybrid RRF, hybrid weighted,
+rerank, +HyDE) on hit@k, recall, MRR, NDCG and precision@k against a hand-written
golden set in `evaluation/dataset.py`. Results land in `eval_results/` as JSON so
runs can be diffed. Start tuning here — it is deterministic and nearly free.

## Configuration

All settings live in `.env` (see `.env.example`). The knobs that matter most:

| Variable | Default | Effect |
| --- | --- | --- |
| `HYBRID_DENSE_WEIGHT` | `0.7` | Raise for paraphrased questions, lower for exact error strings and resource names |
| `RETRIEVE_TOP_K` / `RERANK_TOP_N` | `20` / `5` | Candidate pool vs. what reaches the prompt |
| `CRAG_RELEVANCE_FLOOR` | `0.25` | Below this the context is declared insufficient without a grader call |
| `SEMANTIC_CACHE_THRESHOLD` | `0.95` | Lower reuses answers more aggressively |
| `LLM_EFFORT` | `high` | `low`–`max`; graders always run at `low` on Haiku |
| `ENABLE_HYDE` / `_CRAG` / `_SELF_RAG` / `_TEXT2SQL` / `_GUARDRAILS` / `_CACHE` | `true` | Turning a flag off removes those nodes from the compiled graph |

Turning the flags off reconstructs the baseline RAG pipeline the advanced layers
were added to, which is the fastest way to measure what each one buys.

## Measured retrieval results

`python -m advanced_rag.evaluation.runner --retrieval`, 23 labelled cases
(15 standard + 8 paraphrase-only *hard* cases), k=5, Gemini `gemini-embedding-001`
dense (dim=768) + local BM25 sparse, with the Gemini LLM-scored reranker:

| Strategy | p@5 | hit@5 | recall | MRR | NDCG | hard MRR (n=8) | time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dense only | 0.77 | 1.00 | 1.00 | 0.96 | 0.97 | 0.88 | 17.7s |
| sparse only (BM25) | 0.52 | 0.91 | 0.91 | 0.79 | 0.82 | 0.40 | 0.1s |
| hybrid (RRF) | 0.66 | 1.00 | 1.00 | 0.98 | 0.98 | 0.94 | 12.5s |
| hybrid (weighted) | 0.75 | 1.00 | 1.00 | 0.98 | 0.98 | 0.94 | 12.1s |
| **hybrid + rerank** | **0.83** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** | 83.9s |
| hybrid + rerank + HyDE | 0.79 | 0.96 | 0.96 | 0.96 | 0.96 | 0.88 | 163.0s |

What this run establishes:

- **The authoritative Gemini reranker is the clear winner.** It tops every metric
  (p@5 0.83, MRR/NDCG 1.00, and 1.00 on the hard subset). This is the opposite of
  the lexical stand-in reranker used when the local fastembed models can't
  download — that one is *harmful* and stays score-only (see below).
- **The hard subset is what now separates the strategies.** On the 15 standard
  cases hit/recall are saturated near 1.00; the 8 paraphrase-only cases discriminate.
  Hybrid lifts hard MRR from dense's 0.88 to 0.94, reranking takes it to 1.00, and
  sparse collapses to 0.40 — the two arms are finding genuinely different passages.
- **Weighted fusion keeps the edge on p@5** (0.75 vs RRF 0.66) at equal MRR, which
  is why `HYBRID_DENSE_WEIGHT` stays a dial rather than settling for weight-free RRF.
- **HyDE costs more than it returns here** — it doubles latency to 163s and drops
  hard MRR back to 0.88 (Gemini quota 429s during the run degraded it further), so
  it is off the default path.

Reranking latency (83.9s) is dominated by one LLM call per candidate plus 429
back-off; the fast path uses hybrid weighted and reranks only when enabled.

### Out-of-corpus abstention

Three negative queries with no supporting document all abstained — top retrieval
score ≈0.002, far under the 0.25 CRAG floor:

- "What is the maximum number of pods per node on our cluster…"
- "How do I rotate the etcd data-encryption keys?"
- "What is our disaster-recovery procedure for a full etcd…"

### A weak score must not drive decisions

The lexical fallback scorer is useful to look at and useless to act on, and that
distinction has to be enforced in two separate places — both were originally
wrong:

1. **Ordering.** Letting it reorder dropped p@5 from 0.76 to 0.64, so it scores
   but does not sort.
2. **The CRAG relevance floor.** Gating on it was worse. Measured against the
   live service, four of four semantically-phrased queries scored 0.005–0.133
   *while retrieval had returned exactly the right document*:

   | Query | Top source | rerank | retrieval |
   | --- | --- | --- | --- |
   | "why was my container killed for using too much RAM?" | `oomkilled.md` ✓ | 0.133 | 1.000 |
   | "my service went down right after I shipped a release" | `postmortem-…` ✓ | 0.005 | 0.700 |
   | "the machine ran out of space…" | `node-notready.md` ✓ | 0.008 | 0.955 |
   | "who has to sign off before I take servers out of rotation?" | `cluster-policy.md` ✓ | 0.008 | 0.700 |

   Every one tripped the 0.25 floor, burning a corrective rewrite and then
   instructing `generate` to hedge about context that was correct — i.e. the
   failure landed hardest exactly where dense retrieval was working best.

`RetrievedChunk.rerank_is_authoritative` now marks whether a score came from a
real cross-encoder. `score` falls back to the retrieval score when it did not,
and the CRAG floor only consults authoritative scores. The API returns
`rerank_is_authoritative` / `ordered_by` so a client can label its own columns
honestly.

## Logging degradations

Every LLM- and model-dependent stage fails open, which is right for an on-call
tool — but `logger.exception` on an expected fallback is a defect, not diligence.
HyDE failing across 15 eval cases emitted 15 identical 40-line tracebacks and
buried the results table entirely.

`observability.log_degraded()` reports the first occurrence of each distinct
degradation in full (traceback at DEBUG) and every repeat as a single line tagged
`(repeat)`. Same run afterwards: 0 tracebacks, 1 full warning, 14 one-liners, and
a readable table.

## Cache correctness

Two rules the cache has to obey, both learned by running the service rather than
by reading it:

- **A failed generation is never cached.** `generate_node` sets
  `generation_failed` when the model call raises for an infrastructure reason, and
  `finalize_node` refuses to store the run. Without this, one credential or
  network blip is remembered as the answer and replayed to every identical
  question for `CACHE_TTL_SECONDS`.
- **Citations are stored with the answer.** They are not derivable on a cache hit,
  because a hit skips retrieval entirely — so an answer whose text says `[1]`
  would come back with an empty sources panel.

## Cost notes

- The frozen system prompts in `llm/prompts.py` are module-level constants and are
  sent with `cache_control` — do not interpolate per-request values into them or
  the prompt cache silently stops hitting. Check `usage.cache_read_input_tokens`.
- Grader nodes (router, CRAG, Self-RAG, guardrail classifiers) run on
  `LLM_FAST_MODEL` at `effort=low` with JSON-schema output; only answer generation
  uses the full model at `LLM_EFFORT`.
- A worst-case run makes ~8 model calls (route, HyDE, grade, rewrite, generate ×2,
  critique, 2 guardrail classifiers). The cache and the rerank-score floor exist
  to keep the common case well below that.

## Project layout

```
src/advanced_rag/
  config.py            settings; blank service URLs select local fallbacks
  models.py            shared pydantic models
  cache.py             exact + semantic answer cache
  observability.py     logging and per-node timing
  llm/                 Anthropic client wrapper + frozen prompts
  ingestion/           chunker, loader, `rag-ingest` CLI
  retrieval/           embeddings, Qdrant hybrid store, retriever (HyDE/rerank)
                       bm25.py = pure-Python BM25 backend, no model download
  text2sql/            schema introspection, generator, validator, executor
  guardrails/          regex detectors + the 9-layer pipeline
  graph/               state, nodes, builder, public `ask()`/`resume()`
  evaluation/          golden dataset, metrics, runner
  api/                 FastAPI app
data/corpus/           8 Kubernetes runbooks, a postmortem and a policy doc
ui/web/                front end: index.html, styles.css, app.js, markdown.js
                       USWDS-styled, served as static files by the API; no npm,
                       no build, no webfont or CDN request
tests/                 offline suite (no API key needed)
```

## Status

Verified on this machine:

- Chunking: 9 documents → 49 chunks (median 415 chars).
- Graph compiles with all 13 nodes; the offline test suite covers the CRAG and
  Self-RAG loop bounds, the SQL approval gate refusing to execute, guardrail
  block/allow behaviour, SQL validation, cache tiers, retrieval fusion arithmetic,
  the LLM client's per-model capability handling, and the API contract.
- Text2SQL end-to-end against SQLite, including that a write is refused and the
  table survives.

- Live retrieval with `RETRIEVAL_BACKEND=keyword`: 49 chunks indexed into Qdrant
  and queried, **15/15 hit@5** on the labelled golden set, metadata filters
  working.
- The service running under uvicorn, driven over HTTP: `/health` reports `ok`
  with 49 chunks, `/retrieve` returns scored passages, `/ask` blocks a secret
  request and a prompt injection, redacts PII and answers, `mode=hybrid`
  degrades to sparse instead of erroring, bad input is a 422, unknown thread is
  a 404, `/docs` serves the OpenAPI UI, and the exact-match cache hits on a
  repeat (239 ms → 52 ms).
- The CRAG corrective loop end-to-end against real retrieval: the trace shows
  `retrieve → grade → rewrite_query → retrieve → grade → generate`, stopping at
  the 2-pass bound.
- Both cache rules above, verified against the live service: a repeated question
  whose generation failed stays `cached=False` and re-retrieves, and the response
  still carries its 5 citations.
- Rerank scores are differentiated (4 distinct of 5) after the IDF-weighting fix,
  and guardrail reporting reads `6 of 9 layers ran; 3 skipped` instead of
  claiming nine passes.
- Offline mode end to end against the running service with `LLM_PROVIDER=offline`:
  `/health` reports `llm_mode: offline`, a question returns a 5-citation
  extractive answer with `extractive: true` and 0 tokens, the trace reads
  `route: offline mode - vector retrieval only` through
  `generate: extractive - 2103 chars, no LLM`, `finalize: cached` (and the
  repeat is a 9 ms exact hit that keeps `extractive: true`), guardrails read
  `6 of 9 layers ran` with `no LLM configured (offline mode)` on the three
  skips, `injection` and `secret_request` still block, and the log contains
  **zero tracebacks** — down from seven per request.
- The web UI served by the API at `/`, driven headlessly against the running
  service: `/`, `/styles.css`, `/app.js` and `/markdown.js` served, `/docs` and
  the API routes unaffected by the static mount, and the health panel, chat turn,
  citations, `6 of 9 layers ran; 3 skipped` guardrail warning, pipeline trace and
  retrieval lab all rendering real payloads. The SQL approval gate and result
  table were exercised with stubbed responses - the live router needs an LLM.

Not yet verified here:

- Dense retrieval and cross-encoder reranking — `huggingface.co` is blocked on
  this network, so the ONNX models cannot be downloaded (see above).
- Anything that calls the LLM: routing, HyDE, CRAG grading, Self-RAG critique,
  answer generation, and guardrail layers 5, 6 and 9. No `ANTHROPIC_API_KEY` has
  been exercised. `python scripts/smoke.py` closes that gap once a key is set.

## Known gaps

- The checkpointer is `MemorySaver`, so approval state is lost on restart and does
  not survive more than one API worker. Swap in `langgraph-checkpoint-postgres`
  for a real deployment.
- Embedded Qdrant takes an exclusive lock on `data/qdrant`, so `rag-ingest`,
  `rag-api` and the evaluation runner cannot overlap in the no-Docker setup. Stop
  the API before re-ingesting or evaluating, or run the Qdrant container and set
  `QDRANT_URL`. Hitting this raises `EmbeddedStoreBusyError`, which names both
  ways out.
- `pending_approval()` returns the first pending interrupt only.
- Payload indexes are a no-op in embedded Qdrant (it warns on creation). Metadata
  filtering still works, just without the index; run the container for indexed
  filtering.
- The corpus is a small hand-written sample, not a real documentation set.
- Ragas metrics re-retrieve context rather than reusing what the run actually saw.


## Run

The `rag-ingest` / `rag-api` console scripts and their `python -m` equivalents
are interchangeable. The module form below is what was verified on this machine
(Windows, `.venv`, `huggingface.co` blocked).

**Setup (once):**

```powershell
uv venv --python 3.13
uv pip install -e ".[eval,dev]"      # or: pip install -e ".[eval,dev]"
Copy-Item .env.example .env          # then edit as below
```

Because `huggingface.co` is blocked here, force the pure-Python BM25 backend
(no model download, no key) via `.env` or an environment variable:
`RETRIEVAL_BACKEND=keyword`.

**Index the corpus + seed the SQLite ops database:**

```powershell
python -m advanced_rag.ingestion.cli --seed-sql
# → 8 documents -> 49 chunks; indexed 49 chunks into 'k8s_ops'
#   seeded operations database (SQLite): 416 rows
```

**Serve the API + web UI** (http://127.0.0.1:8000, OpenAPI at `/docs`):

```powershell
python -m advanced_rag.api.main
```

> If failed : The reason incorrectly set python path
```
export PYTHONPATH=$PYTHONPATH:$(pwd)/src       
python3 -m src.advanced_rag.api.main
```

No API key? Offline mode is supported — answers become cited, quoted passages:

```powershell
$env:LLM_PROVIDER = "offline"; python -m advanced_rag.api.main
```

> **Do not run `rag-ingest` and `rag-api` at the same time.** Embedded Qdrant
> takes an exclusive lock on `data/qdrant`; overlapping runs raise
> `EmbeddedStoreBusyError`. Let the ingest finish before starting the API.

Verified offline end to end: `/health` reports `llm_mode: offline` with 49
indexed chunks, and `POST /ask` returns an `extractive: true` answer with 5
citations.

```
cd "c:/Users/2327238/Documents/dev/ai/Internal Switch/Projects/AdvacedRAG" && ./.venv/Scripts/python.exe -c "from advanced_rag.config import get_settings as g; s=g(); print('host:',s.api_host,'port:',s.api_port)"

```

## Sample Questions: 

1. How many sev1 incidents occurred?
2. How many failed deployments?
3. Show unhealthy nodes



## K8S SRE Uster Interface 
![K8s SRE UI 1](./assets/k8s_sre_ui_1.png)
![K8s SRE UI 2](./assets/k8s_sre_ui_2.png)

---

![K8s SRE QUES_2 UI 3](./assets/k8s_sre_2nd_ques_1.png)
![K8s SRE QUES_2 UI 4](./assets/k8rs_sre_2nd_ques_2.png)
![K8s SRE QUES_2 UI 4](./assets/k8s_sre_2nd_ques_3.png)

---
![k8s SRE QUES_3](./assets/k8s_sre_ques_5_1.png)
![k8s SRE QUES_3](./assets/k8s_sre_ques_5_2.png)
![k8s SRE QUES_3](./assets/k8s_sre_ques_5_3.png)
