# Work log — getting Advanced RAG live on a locked-down network

A record of what changed, the challenges we hit, how we got past each, and what
is still open. Context: a corporate network where `huggingface.co` is blocked and
TLS is proxy-inspected, but the Gemini API is reachable.

---

## 1. What we set out to do

1. **Fix a broken server** — it started but ran with no LLM (offline/extractive)
   and spammed HuggingFace 403 errors.
2. **P0** — restore real semantic retrieval (it had silently degraded to lexical
   BM25).
3. **P1** — robustness (retries) and latency (parallelism).
4. **Deployability** — make it deployable end-to-end from another machine.
5. **P2** — measure the retrieval gain instead of asserting it.

---

## 2. Challenges faced and how we overcame them

### A. LLM was silently offline — four stacked bugs, not one

The server logged `ImportError: cannot import name 'genai'` and fell back to
extractive answers. Peeling it back, there were **four** independent problems:

| # | Challenge | Root cause | Fix |
|---|-----------|-----------|-----|
| 1 | `from google import genai` failed | `google-genai` was never installed and wasn't in `pyproject.toml` | Installed it; recorded as a `[gemini]` optional extra in `pyproject.toml` |
| 2 | Key loaded as `None` | `.env` used `GEMINI_API_KEY`, but the config field `google_api_key` only read `GOOGLE_API_KEY` | Added `AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", …)` in `config.py` |
| 3 | Model call would 404 | `LLM_MODEL=claude-opus-5` — a Claude name pointed at Google's API | Switched to Gemini models in `.env` |
| 4 | Chosen Gemini models rejected | `gemini-2.5-*` is **retired** ("no longer available to new users") and **Pro is quota-blocked** on this key (`429 RESOURCE_EXHAUSTED`) | Probed the key live, settled on `gemini-flash-latest` / `gemini-flash-lite-latest` (the `-latest` aliases dodge future retirements) |

**How we found #4:** rather than guess, we listed the key's available models and
sent one-token test calls to each candidate — that's how we learned Pro 429s and
2.5 is gone.

### B. HuggingFace blocked → retrieval had quietly become BM25-only

Startup spent ~90s retrying `Qdrant/bm25` downloads (403) before falling back.
Worse, the whole "advanced" retrieval stack was running as plain lexical BM25:
no dense arm, and the cross-encoder reranker replaced by a lexical stand-in.

- **First mitigation:** set `RETRIEVAL_BACKEND=keyword` to stop the doomed
  download retries.
- **Then we checked the escape hatches.** The fastembed **GCS mirror** for dense
  models — the documented fallback when HF is blocked — returned **403 here too**.
  So there was no offline path to fastembed's ONNX models.
- **The real fix (P0):** the Gemini **embeddings** API *is* reachable on this
  network. We added a `GeminiEmbedder` (`embeddings.py`) — dense vectors from
  `gemini-embedding-001`, sparse from the existing pure-Python BM25 — selected
  with a new `RETRIEVAL_BACKEND=gemini`. That restored true dense + sparse hybrid
  using only the network path we already had.

### C. Embedding dimensions vs the Qdrant collection

`gemini-embedding-001` emits 3072 dims by default; truncating to a smaller size
(Matryoshka) leaves vectors **un-normalised**, which breaks cosine distance.

- **Fix:** request `output_dimensionality=768` and **L2-normalize** every vector
  in `GeminiEmbedder`. The collection's dense size is baked in at creation, so
  the change required a `rag-ingest --recreate` reindex.

### D. Embedded Qdrant single-process lock

The embedded on-disk store allows exactly one process. The running API server
held the lock, so reindexing and the eval both failed with a busy-store error.

- **Fix (workflow):** stop the server before any reindex/eval, then restart. For
  real deployments this is a non-issue — we use remote Qdrant there (see §3).

### E. Gemini `503 "high demand"` killed generation

With everything wired up, `/ask` retrieved correctly but generation threw
`503 UNAVAILABLE` — twice (the draft and the Self-RAG retry). The Anthropic SDK
auto-retries these; the Gemini client did not.

- **Fix (P1):** added bounded exponential backoff on `429/5xx` to **both** the
  chat path (`GeminiClient._generate`) and the embeddings path
  (`_with_retry`). A demand spike now shows up as latency, not a failed answer.

### F. PowerShell turned a harmless warning into a "failure"

Launching via `run.ps1 … | Tee-Object` reported exit 1. The cause was a benign
`runpy` `RuntimeWarning` on stderr, which PowerShell's stream-merge escalated
into a `NativeCommandError`.

- **Fix:** launch the server through the `rag-api` console-script entry point
  (no `-m` runpy warning) and don't pipe its stderr.

### G. `run.ps1` would silently undo the new backend

The runner force-set `RETRIEVAL_BACKEND=keyword` at runtime and its `-Backend`
validator didn't even allow `gemini`.

- **Fix:** added `gemini` to the validator, made it the default, and made
  `-Offline` force `keyword` (offline = no network, so no dense arm).

### H. Latency ~90s

Two compounding causes: the 503-retry backoff during the spike, and ~6–8
*sequential* Gemini calls per question.

- **Fix (P1):** the two genuinely independent grader calls — the **intent** and
  **scope** guardrails — now run concurrently on a thread pool. `guardrail_input`
  dropped from a would-be ~2 calls to ~1 (measured ~1.26s warm).
- **Honest limit:** the rest of the graph (route → retrieve → grade → generate →
  critique) is a true data-dependency chain and can't be parallelized without
  changing behavior. The dominant cost is the generation call itself.

### I. The eval couldn't show the gain (saturation)

The golden set was **saturated**: on 8 lexically-distinctive docs, plain BM25
already scored `hit@5 = recall = MRR = NDCG = 1.00`. Running it as-is would have
produced a misleading table of 1.00s.

- **Fix (P2):** added 8 **paraphrase-only** `HARD_RETRIEVAL` cases — each worded
  with none of the target document's vocabulary — so BM25 has nothing to match
  and only dense retrieval can find the source. This de-saturated the metrics and
  made the gain visible (numbers in §5).

### J. No Docker on this machine

We couldn't `docker build` / `compose up` here to prove the deployment.

- **How we de-risked it:** verified everything checkable *without* Docker — the
  wheel builds with the **UI bundled** and both console scripts present, the
  Compose YAML **parses** with correctly-merged env, and the app runs
  **env-only**. The one step left for the deploy host is the actual
  `docker compose --profile app up --build`.

### K. Minor snags

- **pytest** hit a Windows temp-dir `PermissionError` on the shared
  `pytest-current` symlink → re-ran with a fresh `--basetemp`. **195 passed.**
- **Buffered stdout** hid eval progress when piping through `grep` → waited for
  completion rather than chasing interim output.

### L. TLS context baked in before the trust store (found via the answer eval)

Adding the Gemini reranker and running the **answer** eval surfaced a latent bug:
`CERTIFICATE_VERIFY_FAILED`. The eval runner never called
`enable_system_trust_store()`, and in the answer path the **guardrail LLM call
fires first** — constructing `GeminiClient`'s httpx SSL context from the certifi
bundle *before* any `GeminiEmbedder` injected the OS trust store. httpx builds
its SSL context at construction time, so that client could never verify the
corporate CA. (The retrieval/reranker evals worked only by luck: embeddings ran
first and patched SSL before the client existed.)

- **Fix:** `GeminiClient.__init__` now injects the trust store *before* building
  its client (mirroring `GeminiEmbedder`), and the eval runner calls it at
  startup like the API and ingest CLI. Root-caused, not patched around.

---

## 3. What changed (by file)

**Core fixes / P0**
- `src/advanced_rag/config.py` — `GEMINI_API_KEY` alias; `gemini` backend;
  `embed_model` / `embed_dim` (768) settings.
- `src/advanced_rag/retrieval/embeddings.py` — new `GeminiEmbedder` (dense Gemini
  + BM25 sparse, L2-normalize, retry); wired into `get_embedder()` /
  `get_reranker()`.
- `.env` — `LLM_PROVIDER=gemini`, `gemini-flash-latest` / `-flash-lite-latest`,
  `RETRIEVAL_BACKEND=gemini` (Claude kept as a one-line commented alternative).

**P1**
- `src/advanced_rag/llm/gemini_client.py` — `_generate()` retry/backoff on
  429/5xx.
- `src/advanced_rag/guardrails/pipeline.py` — intent + scope run concurrently.

**Deployability**
- `Dockerfile` — multi-stage, non-root, healthcheck, gemini backend (no HF
  downloads).
- `.dockerignore` — small context; never ships `.env`/venv/data.
- `docker-compose.yml` — added `ingest` (one-shot) + `api` services behind an
  `app` profile; infra-only `docker compose up` unchanged.
- `DEPLOY.md` — full deployment guide (env table, standalone/registry/k8s path,
  ops caveats).
- `run.ps1` — `gemini` backend support + default; `-Offline` forces `keyword`.

**P2**
- `src/advanced_rag/evaluation/dataset.py` — 8 `HARD_RETRIEVAL` paraphrase cases.
- `src/advanced_rag/evaluation/runner.py` — hard-subset breakdown in the table.
- `eval_results/` — saved JSON runs (gitignored).

---

## 4. How to run / verify

```bash
# Local (Windows): the one-command runner (defaults to the gemini backend now)
./run.ps1

# Health
curl -s http://127.0.0.1:8000/health          # status ok, llm_mode live, model gemini-flash-latest

# Reindex after any embedding/backend/dim change
./.venv/Scripts/python -m advanced_rag.ingestion.cli --recreate --seed-sql

# Retrieval eval (BM25 vs dense vs hybrid; stop the server first — embedded-store lock)
RETRIEVAL_BACKEND=gemini ./.venv/Scripts/python -m advanced_rag.evaluation.runner --retrieval

# Full deploy on a Docker host (not this machine)
GEMINI_API_KEY=... docker compose --profile app up --build
```

---

## 5. Results (measured)

**Retrieval — hard subset (8 paraphrase-only cases), gemini backend:**

| Metric | Keyword / BM25 (before) | Gemini hybrid (weighted) | Hybrid + HyDE |
|--------|-------------------------|--------------------------|---------------|
| hit@5  | 0.88 (missed 1/8)       | 1.00                     | 1.00          |
| recall@5 | 0.88                  | 1.00                     | 1.00          |
| **MRR** | **0.40**               | **0.94**                 | **1.00**      |

Full 23-case set: BM25 `p@k 0.56, mrr 0.79` → hybrid `p@k 0.81, mrr 0.98`.
Keyword backend collapses every strategy to the BM25 row (no dense arm).

**Reranker (Gemini LLM-scored, authoritative):** `hybrid + rerank` reaches
`MRR = nDCG = 1.00` on both the full set and the hard subset (hard MRR 0.94 →
1.00) — the correct document is ranked first in every case. Cost: each rerank is
an LLM call, so those strategies are much slower under a demand spike.

**Abstention on out-of-corpus negatives:** 5/5 (100%) — the authoritative
reranker scored every out-of-corpus question below the CRAG floor (top 0.002–
0.027 vs 0.25), i.e. the pipeline declines rather than answering from an
unrelated runbook. Not measurable before (the lexical stand-in isn't
authoritative).

**Answer quality (sample):** the one case that completed before a transient DNS
outage on the eval machine scored `fact_coverage = 1.00`; the graceful
degradation (semantic-cache-off, HyDE→raw question) behaved correctly when the
network dropped. A full pass is pending a stable network window.

**Latency (warm, no 503 spike):** `guardrail_input` ~1.3s (parallelized),
per-grader calls ~1.3–2.8s, generation dominates. Under a Gemini demand spike,
retries trade latency for a successful answer instead of a failure; the new
`LLM_MAX_RETRIES` / `LLM_RETRY_FALLBACK_MODEL` knobs bound that.

**Tests:** 195 passed.

---

## 6. Task status

**Done this round**
- [x] **Gemini reranker.** Authoritative LLM-scored reranker (`GeminiReranker`),
      default on the gemini backend (opt out with `RERANK_MODEL=local-lexical`).
      MRR/nDCG → 1.00 (see §5).
- [x] **Latency polish.** `LLM_MAX_RETRIES` + `LLM_RETRY_FALLBACK_MODEL` bound
      the retry cost and allow a fast-model fallback under a demand spike.
- [x] **Multi-replica checkpointer.** `CHECKPOINT_BACKEND=postgres` +
      `[postgres-checkpoint]` extra persists approval state; safe fallback to
      memory. (Untested against a live Postgres — see below.)
- [x] **Corpus / eval hardening.** `NEGATIVE_RETRIEVAL` cases + abstention metric
      in the runner (5/5 abstained).
- [x] **P3 doc drift.** Readme is actually accurate (plain HTML/JS UI); the only
      Streamlit/course-URL drift is in the user-owned `todo.md`, left untouched.
- [x] **TLS-before-client bug** (found via the answer eval) — fixed at the root.

**Still open**
- [ ] **Full Ragas answer-quality pass.** `rag-eval --answers --ragas` needs a
      judge LLM (Ragas defaults to OpenAI; not available here). Either wire a
      Gemini judge via `langchain-google-genai`, or accept the deterministic
      `fact_coverage` / `cited_expected_rate` metrics. A full `--answers` run is
      also pending a stable network window (a DNS blip cut the last one short).
- [ ] **Verify the container on a Docker host.** `docker compose --profile app
      up --build` — build + ingest→api handoff couldn't be exercised on this
      Docker-less machine; artifacts are otherwise validated (wheel builds with
      UI bundled, compose YAML parses, app runs env-only).
- [ ] **Verify the Postgres checkpointer** against a real Postgres + the
      `[postgres-checkpoint]` extra (wired with a safe fallback, but not yet run
      end-to-end).
- [ ] **Corpus growth (optional).** A larger corpus keeps the eval discriminating
      as retrieval improves.
