# Deploying Advanced RAG

End-to-end deployment for the Kubernetes SRE copilot. One image serves the API
and the web UI; a one-shot job builds the index and seeds the ops database.

The image is built for the **gemini backend**, so it downloads **no** ONNX /
HuggingFace models. At runtime it needs only:

- outbound HTTPS to the Gemini API (`generativelanguage.googleapis.com`), and
- a `GEMINI_API_KEY`.

That is the whole external dependency for the model side — everything else
(vector store, SQL, cache) has a bundled fallback and a managed-service option.

---

## 1. Prerequisites

- Docker with Compose v2 (`docker compose`, not the legacy `docker-compose`).
- A **Gemini API key** (Google AI Studio). Note: the key in use during
  development is **Flash-tier only** — Pro models return `429 RESOURCE_EXHAUSTED`,
  which is why both model tiers default to Flash.
- Network egress to `generativelanguage.googleapis.com`.

## 2. Quick start — full stack with Compose

From the project root:

```bash
export GEMINI_API_KEY=your-key-here          # or put it in a .env file next to this
docker compose --profile app up --build -d
```

This brings up Qdrant + Postgres + Redis, runs the **`ingest`** job to build the
index and seed the ops DB, and then starts the **`api`** service once ingest
finishes. `docker compose up` *without* `--profile app` still starts infra only
(the historical behaviour), so local dev on the host is unchanged.

Watch it come up and confirm health:

```bash
docker compose --profile app logs -f ingest      # runs to completion, then exits
curl -s http://localhost:8000/health | jq        # {"status":"ok","indexed_chunks":49,"llm_mode":"live",...}
```

Then open <http://localhost:8000>.

`/health` reports `"status":"degraded"` until ingest has populated the
collection, and `"llm_mode":"offline"` if the key is missing/invalid — use it as
your readiness signal.

## 3. Configuration (environment variables)

Config is read from the environment (12-factor); nothing is baked into the
image. The Compose file sets the service-to-service wiring; you supply the key.

| Variable | Purpose | Default in image |
| --- | --- | --- |
| `APP_ENV` | `development` \| `production` (enforces security controls) | `development` |
| `GEMINI_API_KEY` | **Required.** Gemini API key. Also accepts `GOOGLE_API_KEY`. | — |
| `LLM_PROVIDER` | `gemini` \| `anthropic` \| `groq` \| `ollama` \| `offline` | `gemini` |
| `LLM_MODEL` | Generation model | `gemini-flash-latest` |
| `LLM_FAST_MODEL` | Grader/guardrail model | `gemini-flash-lite-latest` |
| `RETRIEVAL_BACKEND` | `gemini` (dense API + BM25) \| `keyword` \| `fastembed` \| `auto` | `gemini` |
| `EMBED_MODEL` / `EMBED_DIM` | Embedding model + width | `gemini-embedding-001` / `768` |
| `QDRANT_URL` | Vector store; empty ⇒ embedded on-disk | set to `http://qdrant:6333` |
| `POSTGRES_USER` | PostgreSQL user | `postgres` |
| `POSTGRES_PASSWORD` | PostgreSQL password (**required strong in production**) | `postgres` (dev only) |
| `POSTGRES_DB` | PostgreSQL database name | `ops` |
| `POSTGRES_DSN` | Text2SQL DB DSN; empty ⇒ local SQLite (dev only) | auto-assembled from user/pw |
| `REDIS_URL` | Answer cache; empty ⇒ in-process | set to `redis://redis:6379/0` |
| `API_HOST` / `API_PORT` | Bind address | `0.0.0.0` / `8000` |
| `ENABLE_*` | Feature flags (hyde, crag, self_rag, text2sql, guardrails, cache) | all `true` |

> **Production Security Note:**
> When `APP_ENV=production`, the application strictly requires an explicit PostgreSQL DSN with a supported synchronous driver (`postgresql+psycopg`, `postgresql+psycopg2`, or `postgresql`), a valid hostname, database, username, and password. Asynchronous drivers (`asyncpg`), SQLite fallbacks, passwords shorter than 16 characters, and insecure defaults/placeholders (`postgres`, `password`, `changeme`, `your_password`, etc.) are rejected at application startup. Special characters in passwords must be URL-encoded (e.g., `%40` for `@`, `%23` for `#`). Compatible with managed PostgreSQL services (e.g., AWS RDS, Neon, Cloud SQL).

> Changing `EMBED_MODEL`/`EMBED_DIM` or `RETRIEVAL_BACKEND` changes the dense
> vector size, which is baked into the Qdrant collection. Re-run ingest with a
> fresh collection after any such change (see §5).

## 4. Deploying the image on its own (registry / Kubernetes)

The stack does not require Compose. Build and push the image, then point it at
managed services:

```bash
docker build -t <registry>/advanced-rag:<tag> .
docker push <registry>/advanced-rag:<tag>
```

Run the **ingest** job once (same image, overriding the command) against your
managed Qdrant/Postgres, then run the **api** with the same env plus `REDIS_URL`:

```bash
# one-shot ingest
docker run --rm \
  -e APP_ENV=production \
  -e GEMINI_API_KEY -e RETRIEVAL_BACKEND=gemini \
  -e QDRANT_URL=https://qdrant.internal:6333 -e QDRANT_API_KEY \
  -e POSTGRES_DSN=postgresql+psycopg://sre_user:StrongPasswordMin16Chars!@pg.internal:5432/ops \
  <registry>/advanced-rag:<tag> rag-ingest --corpus /app/data/corpus --seed-sql

# long-running API
docker run -d -p 8000:8000 \
  -e APP_ENV=production \
  -e GEMINI_API_KEY -e RETRIEVAL_BACKEND=gemini \
  -e QDRANT_URL=https://qdrant.internal:6333 -e QDRANT_API_KEY \
  -e POSTGRES_DSN=postgresql+psycopg://sre_user:StrongPasswordMin16Chars!@pg.internal:5432/ops \
  -e REDIS_URL=redis://redis.internal:6379/0 \
  <registry>/advanced-rag:<tag>
```

On Kubernetes, model this as a `Job` (ingest) followed by a `Deployment` (api)
with a readiness probe on `GET /health`. See §6 on replica count.

## 5. Re-indexing / re-seeding

Ingest is idempotent for the SQL seed and rebuilds the collection with
`--recreate`:

```bash
docker compose --profile app run --rm ingest rag-ingest --corpus /app/data/corpus --recreate --seed-sql
```

## 6. Operational notes

- **Scaling out (multi-replica).** By default the Text2SQL approval flow parks
  run state in an **in-memory** checkpointer, so `/approve` must land on the same
  replica that served `/ask` (run one replica, or use sticky sessions). To scale
  out, build the image with the extra and turn on the Postgres checkpointer:
  install `.[postgres-checkpoint]` and set `CHECKPOINT_BACKEND=postgres` (it
  reuses `POSTGRES_DSN`). Approval state then persists in Postgres and any
  replica can resume. If the extra or DB is missing it logs a warning and falls
  back to in-memory, so a misconfig degrades to single-replica rather than down.
- **Gemini 503s under load.** `gemini-flash-latest` intermittently returns
  `503 "high demand"`. Both the chat and embeddings paths retry these with
  backoff, so a spike shows up as latency, not failure. If interactive latency
  matters more than always using the primary model, lower the retry ceiling or
  fall back to `gemini-flash-lite-latest` for generation.
- **Corporate TLS interception.** `truststore` validates against the image's OS
  trust store. Behind a TLS-inspecting proxy, add that proxy's CA to the image
  (`COPY corp-ca.crt /usr/local/share/ca-certificates/ && update-ca-certificates`
  in a derived Dockerfile) or mount it and run `update-ca-certificates`.
- **Data persistence.** Qdrant and Postgres use named volumes
  (`qdrant_storage`, `postgres_data`); the index and ops data survive restarts.
  Redis is cache-only (persistence disabled) and safe to lose.
- **Secrets.** `.env` is gitignored and `.dockerignore`d; the key reaches the
  container only as an environment variable. Prefer your platform's secret
  manager over a committed `.env`.
