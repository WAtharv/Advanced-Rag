# Enterprise Advanced RAG in LangGraph

A production-grade Enterprise RAG system for Kubernetes IT operations, built with LangGraph, FastAPI, Qdrant, PostgreSQL, and Redis — covering hybrid search, reranking, HyDE, CRAG, Self-RAG, Text2SQL, caching, and guardrails.

> Course reference: [Enterprise Advanced RAG with Hybrid Search, ReRanking, HyDE, CRAG, Self-RAG…](https://www.krishnaik.in/project/) — 39 lectures
>
> _Note: the original course URL was truncated in the source material; replace the link above with the full URL._

## Overview

The project starts from a baseline RAG pipeline and incrementally layers on advanced retrieval and safety patterns until it becomes a Kubernetes SRE copilot:

1. Baseline RAG
2. Hybrid search (dense + sparse retrieval)
3. Reranking
4. HyDE (Hypothetical Document Embeddings)
5. CRAG (Corrective RAG)
6. Self-RAG
7. Text2SQL with human-in-the-loop approval
8. Evaluation
9. A 9-layer guardrails pipeline

## What You Will Learn

- Advanced RAG design: hybrid search, reranking, HyDE, CRAG, Self-RAG, and Text2SQL
- LangGraph orchestration for multi-step, stateful retrieval workflows
- Caching strategies, evaluation methodology, and guardrail design

## What You'll Build

A production-grade Kubernetes SRE copilot featuring:

- **FastAPI** service layer
- **LangGraph** orchestration of the RAG workflow
- **Qdrant** vector retrieval with hybrid search and reranking
- **PostgreSQL** Text2SQL with human approval before execution
- **Redis** caching
- **Streamlit** UI
- **Ragas** evaluations
- **Security layers** — a 9-layer guardrails pipeline

## Tech Stack

| Layer | Technology |
| --- | --- |
| Orchestration | LangGraph |
| API | FastAPI |
| Vector store | Qdrant |
| Relational store / Text2SQL | PostgreSQL |
| Cache | Redis |
| UI | Streamlit |
| Evaluation | Ragas |

## Getting Started

_Setup instructions to be added as the implementation lands._

## Improvement roadmap

On this network huggingface.co is blocked, so retrieval silently degrades to
lexical BM25 only (no dense arm, cross-encoder replaced by a lexical stand-in).
Gemini's API — including embeddings — is reachable, so it becomes the on-network
path back to real semantic retrieval.

- [x] **P0 — Restore semantic retrieval via Gemini embeddings.** Added a
      `GeminiEmbedder` (dense = `gemini-embedding-001` at 768 dims, sparse =
      local BM25) selected with `RETRIEVAL_BACKEND=gemini`, reindexed. Retrieval
      is true dense + sparse hybrid again; verified a paraphrase-only query
      surfaces the right runbook that BM25 alone buried.
  - [x] _(follow-up, done)_ Gemini-backed authoritative reranker replaces the
        lexical stand-in — MRR/nDCG reach 1.00; also powers a 5/5 abstention
        check on out-of-corpus questions.
- [x] **P1 — Robustness & latency.** Retry/backoff on Gemini 429/5xx added to
      both the chat and embeddings paths (`gemini-flash-latest` 503s ride
      through as latency, not failure). Intent + scope guardrail calls now run
      concurrently — `guardrail_input` dropped from ~2 sequential calls to ~1.
      (The remaining grader calls are a data-dependency chain; not parallelizable
      without changing semantics.)
- [x] **Deployability — end-to-end.** `Dockerfile` (multi-stage, non-root,
      healthcheck, gemini backend so no HF downloads), `.dockerignore`,
      `docker-compose` app + one-shot ingest services behind an `app` profile,
      and `DEPLOY.md`. Verified as far as this Docker-less machine allows: wheel
      builds with the UI bundled, compose YAML parses, app runs env-only.
- [x] **P2 — Measure it.** Added 8 paraphrase-only `HARD_RETRIEVAL` cases (the
      existing golden set was saturated — BM25 already scored 1.00 on it). On
      that hard subset the keyword→hybrid gain is now measured: MRR **0.40 → 0.94**
      (hybrid weighted), and **1.00** with HyDE; BM25 also missed 1/8 docs
      entirely (hit@5 0.88 → 1.00). Keyword backend collapses every strategy to
      the BM25 row. Results in `eval_results/`. (Ragas answer-quality pass still
      optional via `--answers --ragas`.)
- [ ] **P3 — Doc drift.** README/overview still advertises a Streamlit UI and a
      truncated course URL; the real UI is plain HTML/JS served by FastAPI.
