# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Advanced RAG - production image.
#
# Serves the API + bundled web UI from one process (`rag-api`). Ships the same
# entry points as a source checkout, so `rag-ingest` runs from this image too
# (the compose `ingest` job uses exactly that).
#
# Built for the gemini backend: no ONNX/HuggingFace models are downloaded, so
# the image needs only outbound HTTPS to the Gemini API at runtime. Switch to
# the fastembed backend only on a host that can reach huggingface.co.
# ---------------------------------------------------------------------------

# ----------------------------------------------------------------- builder
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# hatchling builds from the source tree, and force-includes ui/web into the
# wheel, so the UI ends up inside the installed package. Copy everything the
# build reads before installing.
COPY pyproject.toml Readme.md ./
COPY src ./src
COPY ui ./ui

# Install into a self-contained venv we can lift into the runtime stage. The
# [gemini] extra pulls in google-genai; the base deps cover retrieval, the API
# and the local BM25 sparse arm.
RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir ".[gemini]"

# ----------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm AS runtime

# ca-certificates: the Gemini calls are HTTPS; truststore validates against this
# OS trust store. On a TLS-inspecting corporate network, add that proxy's CA to
# the image (COPY it into /usr/local/share/ca-certificates + update-ca-certificates).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=builder /venv /venv

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Container-safe defaults; override per deployment (see DEPLOY.md).
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    LLM_PROVIDER=gemini \
    RETRIEVAL_BACKEND=gemini

WORKDIR /app
COPY data/corpus/ ./data/corpus/
# Only used by the embedded-store / SQLite fallbacks; a remote-services
# deployment never writes here. Owned by the non-root user so both paths work.
RUN mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8000

# Liveness against the app's own /health. start-period covers graph compile on
# boot; the endpoint reports "degraded" until the collection has been ingested.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

CMD ["rag-api"]
