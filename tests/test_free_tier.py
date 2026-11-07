"""Tests for Stage 1 — Free-First AI Architecture.

Verifies:
1. Groq hosted LLM provider (completions, structured JSON, bounded retry, error handling).
2. Ollama local LLM provider (health check, completions, structured JSON, connection error handling).
3. FastEmbed 384-dimensional dense embeddings (BAAI/bge-small-en-v1.5) + pure-Python BM25.
4. FastEmbed CrossEncoder reranker (Xenova/ms-marco-MiniLM-L-6-v2) and graceful lexical fallback.
5. Qdrant 384-dim collection compatibility and baseline fallback.
6. LangGraph execution using free providers and offline mode.
7. Strict isolation: no paid API calls (Anthropic/Gemini) in Groq/Ollama/offline modes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from advanced_rag.config import Settings, get_settings
from advanced_rag.graph import nodes
from advanced_rag.graph.state import initial_state
from advanced_rag.llm.client import (
    LLMClient,
    LLMResult,
    LLMUnavailableError,
    get_llm,
    llm_available,
    reset_llm,
)
from advanced_rag.llm.groq_client import GroqClient
from advanced_rag.llm.json_repair import clean_json_text, parse_and_validate_json
from advanced_rag.llm.ollama_client import OllamaClient
from advanced_rag.models import Route, Verdict
from advanced_rag.retrieval.embeddings import (
    Embedder,
    LexicalReranker,
    Reranker,
    get_embedder,
    get_reranker,
    sigmoid,
)
from advanced_rag.retrieval.vectorstore import DENSE, SPARSE, HybridStore


# -----------------------------------------------------------------------------
# Test Models
# -----------------------------------------------------------------------------
class SampleDecision(BaseModel):
    action: str = Field(description="Action name")
    score: float = Field(description="Score between 0.0 and 1.0")
    reason: str = Field(description="Explanation")


# -----------------------------------------------------------------------------
# Part 1: JSON Repair & Robust Parsing
# -----------------------------------------------------------------------------
def test_clean_json_text_strips_markdown_fences():
    raw = "```json\n{\n  \"action\": \"restart\",\n  \"score\": 0.95,\n  \"reason\": \"OOMKilled\"\n}\n```"
    cleaned = clean_json_text(raw)
    assert cleaned.startswith("{") and cleaned.endswith("}")
    parsed = parse_and_validate_json(raw, SampleDecision)
    assert parsed.action == "restart"
    assert parsed.score == 0.95


def test_clean_json_text_handles_preamble_and_trailing_commas():
    raw = (
        "Here is the requested decision in JSON:\n"
        "{\n"
        "  \"action\": \"scale_up\",\n"
        "  \"score\": 0.8,\n"
        "  \"reason\": \"High CPU\",\n"
        "}\n"
        "Hope this helps!"
    )
    parsed = parse_and_validate_json(raw, SampleDecision)
    assert parsed.action == "scale_up"
    assert parsed.score == 0.8


# -----------------------------------------------------------------------------
# Part 2: Groq API Integration
# -----------------------------------------------------------------------------
def test_groq_client_inherits_llm_client():
    settings = Settings(llm_provider="groq", groq_api_key="gsk_test_key_123")
    client = GroqClient(settings)
    assert isinstance(client, LLMClient)
    assert client.provider == "groq"


def test_groq_client_missing_key_raises_unavailable():
    settings = Settings(llm_provider="groq", groq_api_key=None)
    client = GroqClient(settings)
    with pytest.raises(LLMUnavailableError) as exc_info:
        client.complete("hello")
    assert "GROQ_API_KEY is not configured" in str(exc_info.value)


def test_groq_client_normal_completion():
    settings = Settings(llm_provider="groq", groq_api_key="gsk_test_key")
    client = GroqClient(settings)

    mock_resp = MagicMock()
    mock_resp.model = "llama-3.3-70b-versatile"
    mock_choice = MagicMock()
    mock_choice.message.content = "Restart the pod with kubectl delete pod."
    mock_choice.finish_reason = "stop"
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 15
    mock_resp.usage.completion_tokens = 10

    with patch.object(client._client.chat.completions, "create", return_value=mock_resp) as mock_create:
        res = client.complete("How to fix CrashLoopBackOff?")
        assert res.text == "Restart the pod with kubectl delete pod."
        assert res.stop_reason == "end_turn"
        assert res.input_tokens == 15
        assert res.output_tokens == 10
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["model"] == "llama-3.3-70b-versatile"


def test_groq_client_structured_json():
    settings = Settings(llm_provider="groq", groq_api_key="gsk_test_key")
    client = GroqClient(settings)

    json_payload = '{"action": "rollback", "score": 0.99, "reason": "Failed deployment"}'
    mock_resp = MagicMock()
    mock_resp.model = "llama-3.1-8b-instant"
    mock_choice = MagicMock()
    mock_choice.message.content = f"```json\n{json_payload}\n```"
    mock_choice.finish_reason = "stop"
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 20
    mock_resp.usage.completion_tokens = 12

    with patch.object(client._client.chat.completions, "create", return_value=mock_resp):
        decision = client.complete_json("Analyze incident", SampleDecision)
        assert isinstance(decision, SampleDecision)
        assert decision.action == "rollback"
        assert decision.score == 0.99
        assert decision.reason == "Failed deployment"


def test_groq_client_rate_limit_retry():
    import groq

    settings = Settings(llm_provider="groq", groq_api_key="gsk_test_key", llm_max_retries=2)
    client = GroqClient(settings)

    # First attempt raises RateLimitError, second succeeds
    mock_err_response = MagicMock(status_code=429)
    rate_err = groq.RateLimitError("Rate limit reached", response=mock_err_response, body=None)

    mock_resp = MagicMock()
    mock_resp.model = "llama-3.3-70b-versatile"
    mock_choice = MagicMock()
    mock_choice.message.content = "Success after backoff"
    mock_choice.finish_reason = "stop"
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 10
    mock_resp.usage.completion_tokens = 5

    with patch.object(
        client._client.chat.completions, "create", side_effect=[rate_err, mock_resp]
    ) as mock_create:
        with patch("time.sleep", return_value=None):
            res = client.complete("test query")
            assert res.text == "Success after backoff"
            assert mock_create.call_count == 2


# -----------------------------------------------------------------------------
# Part 3: Ollama Local LLM Integration
# -----------------------------------------------------------------------------
def test_ollama_client_inherits_llm_client():
    settings = Settings(
        llm_provider="ollama",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
    )
    client = OllamaClient(settings)
    assert isinstance(client, LLMClient)
    assert client.provider == "ollama"


def test_ollama_client_is_available():
    settings = Settings(llm_provider="ollama")
    client = OllamaClient(settings)

    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        assert client.is_available() is True

    with patch("httpx.Client.get") as mock_get:
        import httpx

        mock_get.side_effect = httpx.ConnectError("Connection refused")
        assert client.is_available() is False


def test_ollama_client_completion():
    settings = Settings(llm_provider="ollama")
    client = OllamaClient(settings)

    mock_data = {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": "Check container memory limit in pod spec."},
        "done": True,
        "prompt_eval_count": 18,
        "eval_count": 9,
    }

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = mock_data

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        res = client.complete("Why did container terminate with exit code 137?")
        assert "memory limit" in res.text
        assert res.input_tokens == 18
        assert res.output_tokens == 9
        mock_post.assert_called_once()


def test_ollama_client_structured_json():
    settings = Settings(llm_provider="ollama")
    client = OllamaClient(settings)

    mock_data = {
        "model": "llama3.2",
        "message": {
            "role": "assistant",
            "content": '{"action": "cordon_node", "score": 0.88, "reason": "DiskPressure detected"}',
        },
        "done": True,
        "prompt_eval_count": 25,
        "eval_count": 15,
    }

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = mock_data

    with patch("httpx.Client.post", return_value=mock_response):
        decision = client.complete_json("Node health check", SampleDecision)
        assert decision.action == "cordon_node"
        assert decision.score == 0.88


# -----------------------------------------------------------------------------
# Part 4: Local Embedding Architecture (BAAI/bge-small-en-v1.5)
# -----------------------------------------------------------------------------
def test_fastembed_bge_small_embedding_dim():
    settings = Settings(retrieval_backend="fastembed", dense_model="BAAI/bge-small-en-v1.5")
    embedder = Embedder(settings.dense_model, settings.sparse_model)
    assert embedder.supports_dense is True
    assert embedder.dim == 384


def test_fastembed_embeds_query_and_documents():
    settings = Settings(retrieval_backend="fastembed", dense_model="BAAI/bge-small-en-v1.5")
    embedder = Embedder(settings.dense_model, settings.sparse_model)

    query_vec = embedder.embed_query("CrashLoopBackOff error in production")
    assert len(query_vec) == 384
    assert isinstance(query_vec[0], float)

    docs = ["Runbook for container crash troubleshooting", "NodeNotReady resolution guide"]
    doc_vecs = embedder.embed_documents(docs)
    assert len(doc_vecs) == 2
    assert len(doc_vecs[0]) == 384
    assert len(doc_vecs[1]) == 384


def test_sparse_bm25_alongside_dense():
    settings = Settings(retrieval_backend="fastembed", dense_model="BAAI/bge-small-en-v1.5")
    embedder = Embedder(settings.dense_model, settings.sparse_model)

    sparse_q = embedder.sparse_query("CrashLoopBackOff")
    assert len(sparse_q.indices) > 0
    assert len(sparse_q.values) == len(sparse_q.indices)


# -----------------------------------------------------------------------------
# Part 5: Local Reranker (ms-marco-MiniLM-L-6-v2) & Graceful Fallback
# -----------------------------------------------------------------------------
def test_cross_encoder_reranker_scores():
    reranker = Reranker("Xenova/ms-marco-MiniLM-L-6-v2")
    assert reranker.is_cross_encoder is True

    scores = reranker.score(
        "How to fix OOMKilled?",
        [
            "A container is OOMKilled when it exceeds memory limits. Increase limits.",
            "PostgreSQL index maintenance and query optimization.",
        ],
    )
    assert len(scores) == 2
    assert isinstance(scores[0], float)
    # The relevant passage should score significantly higher than the unrelated passage
    assert scores[0] > scores[1]

    # Sigmoid calibration
    calibrated = [sigmoid(s) for s in scores]
    assert 0.0 <= calibrated[0] <= 1.0
    assert 0.0 <= calibrated[1] <= 1.0


def test_reranker_graceful_lexical_fallback():
    # If an unavailable model is configured, get_reranker() falls back to LexicalReranker
    mock_settings = Settings(rerank_model="non_existent_model_path_12345")
    with patch("advanced_rag.retrieval.embeddings.get_settings", return_value=mock_settings):
        get_reranker.cache_clear()
        reranker = get_reranker()
        assert isinstance(reranker, LexicalReranker)
        scores = reranker.score("pod crash", ["pod crash issue", "unrelated doc"])
        assert len(scores) == 2
        get_reranker.cache_clear()


# -----------------------------------------------------------------------------
# Part 6: Qdrant 384-Dim Collection & Baseline Safety
# -----------------------------------------------------------------------------
def test_qdrant_collections_exist_and_safe():
    store = HybridStore()
    try:
        client = store.client
        # Verify baseline collection was preserved
        assert client.collection_exists("k8s_ops")
        assert client.count("k8s_ops").count == 82

        # Verify 384-dim collection exists
        assert client.collection_exists("k8s_ops_bge_small")
        bge_info = client.get_collection("k8s_ops_bge_small")
        assert client.count("k8s_ops_bge_small").count == 82
        vectors = bge_info.config.params.vectors
        assert DENSE in vectors
        assert vectors[DENSE].size == 384
    finally:
        store.close()


def test_qdrant_hybrid_search_bge_small():
    settings = Settings(
        qdrant_collection="k8s_ops_bge_small",
        retrieval_backend="fastembed",
        dense_model="BAAI/bge-small-en-v1.5",
        sparse_model="local-bm25",
    )
    store = HybridStore(settings=settings, collection="k8s_ops_bge_small")
    try:
        results = store.search("OOMKilled container memory limits", top_k=3, mode="hybrid")
        assert len(results) > 0
        top = results[0]
        assert top.chunk.text != ""
        assert top.retrieval_score > 0.0
    finally:
        store.close()


def test_qdrant_missing_collection_raises_when_fallback_disabled():
    # By default, allow_collection_fallback=False; a missing collection must fail loudly
    settings = Settings(
        qdrant_collection="non_existent_collection",
        retrieval_backend="fastembed",
        allow_collection_fallback=False,
    )
    store = HybridStore(settings=settings, collection="non_existent_collection")
    try:
        with pytest.raises(RuntimeError) as exc_info:
            store.search("CrashLoopBackOff", top_k=2)
        assert "does not exist" in str(exc_info.value)
    finally:
        store.close()


def test_qdrant_fallback_to_k8s_ops_baseline_when_explicitly_allowed():
    # When explicitly allowed via allow_collection_fallback=True, store falls back to baseline 'k8s_ops'
    settings = Settings(
        qdrant_collection="non_existent_collection",
        retrieval_backend="fastembed",
        sparse_model="local-bm25",
        allow_collection_fallback=True,
    )
    store = HybridStore(settings=settings, collection="non_existent_collection")
    try:
        results = store.search("CrashLoopBackOff", top_k=2)
        assert len(results) > 0
    finally:
        store.close()


# -----------------------------------------------------------------------------
# Part 7: LangGraph Nodes with Free LLM Provider
# -----------------------------------------------------------------------------
def test_langgraph_route_node_with_groq():
    fake_groq = MagicMock(spec=GroqClient)
    from advanced_rag.graph.nodes import RouteDecision

    fake_groq.complete_json.return_value = RouteDecision(
        route="vector", reason="Documentation question"
    )

    with patch("advanced_rag.graph.nodes.get_llm", return_value=fake_groq):
        with patch("advanced_rag.graph.nodes.llm_available", return_value=True):
            state = initial_state("How do I fix CrashLoopBackOff?")
            res = nodes.route_node(state)
            assert res["route"] == Route.VECTOR


def test_langgraph_grade_node_with_groq():
    fake_groq = MagicMock(spec=GroqClient)
    from advanced_rag.graph.nodes import ContextGrade
    from advanced_rag.models import Chunk, RetrievedChunk

    fake_groq.complete_json.return_value = ContextGrade(
        verdict="correct", reason="Runbook covers OOMKilled"
    )

    sample_chunk = RetrievedChunk(
        chunk=Chunk(
            id="test-chunk-1",
            text="A container is OOMKilled when it exceeds memory limits.",
            source="oomkilled.md",
        ),
        retrieval_score=0.9,
    )

    with patch("advanced_rag.graph.nodes.get_llm", return_value=fake_groq):
        with patch("advanced_rag.graph.nodes.llm_available", return_value=True):
            state = initial_state("How do I fix OOMKilled?")
            state["chunks"] = [sample_chunk]
            state["context"] = "[1] oomkilled.md: A container is OOMKilled when it exceeds memory limits."
            res = nodes.grade_node(state)
            assert res["verdict"] == Verdict.CORRECT


# -----------------------------------------------------------------------------
# Part 8: Offline Extractive Mode Behavior
# -----------------------------------------------------------------------------
def test_offline_mode_does_not_call_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    get_settings.cache_clear()
    reset_llm()

    assert llm_available() is False
    with pytest.raises(LLMUnavailableError):
        get_llm()


# -----------------------------------------------------------------------------
# Part 9: Isolation Guarantee — No Paid APIs Called
# -----------------------------------------------------------------------------
def test_groq_mode_never_invokes_anthropic_or_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_valid")
    get_settings.cache_clear()
    reset_llm()

    # If Anthropic or Gemini were called, these mocks would record it
    with patch("anthropic.Anthropic") as mock_anthropic, patch("google.genai.Client") as mock_gemini:
        client = get_llm()
        assert isinstance(client, GroqClient)
        mock_anthropic.assert_not_called()
        mock_gemini.assert_not_called()


# -----------------------------------------------------------------------------
# Part 10: PostgreSQL Credentials Security & Production Validation
# -----------------------------------------------------------------------------
def test_postgres_credentials_local_dev_allowed():
    # In development mode, convenient local SQLite fallback and default compose creds are allowed
    s_sqlite = Settings(app_env="development", postgres_dsn=None)
    assert "sqlite" in s_sqlite.sql_url

    s_dev_pg = Settings(
        app_env="development",
        postgres_dsn="postgresql+psycopg://postgres:postgres@localhost:5432/ops",
    )
    assert "postgres:postgres@" in s_dev_pg.sql_url


def test_postgres_credentials_production_rejects_default_and_requires_strong_dsn():
    # 1. Missing DSN must raise ValueError
    with pytest.raises(ValueError, match="POSTGRES_DSN must be explicitly configured"):
        Settings(app_env="production", postgres_dsn=None)

    # 2. SQLite DSN must raise ValueError
    with pytest.raises(ValueError, match="Production requires a supported synchronous PostgreSQL driver"):
        Settings(app_env="production", postgres_dsn="sqlite:///data/ops.db")

    # 3. Asynchronous driver (asyncpg) must raise ValueError
    with pytest.raises(ValueError, match="Asynchronous PostgreSQL driver 'postgresql\\+asyncpg' is not supported"):
        Settings(
            app_env="production",
            postgres_dsn="postgresql+asyncpg://sre_admin:StrongPasswordLongSecret99!@pg-prod.internal:5432/ops",
        )

    # 4. Missing password must raise ValueError
    with pytest.raises(ValueError, match="must include a non-empty password"):
        Settings(
            app_env="production",
            postgres_dsn="postgresql+psycopg://sre_admin@pg-prod.internal:5432/ops",
        )

    # 5. Insecure placeholder passwords must raise ValueError
    for bad_pw in ("postgres", "password", "changeme", "your_password", "admin", "1234567890123456"):
        with pytest.raises(ValueError, match="cannot be a known default or placeholder"):
            Settings(
                app_env="production",
                postgres_dsn=f"postgresql+psycopg://sre_admin:{bad_pw}@pg-prod.internal:5432/ops",
            )

    # 6. Password under minimum length (16 chars) must raise ValueError
    with pytest.raises(ValueError, match="must be at least 16 characters"):
        Settings(
            app_env="production",
            postgres_dsn="postgresql+psycopg://sre_admin:Only15CharsLong@pg-prod.internal:5432/ops",
        )

    # 7. URL-encoded password with real length < 16 must be rejected
    # "a%40b%23c%24d" has 13 chars raw, decodes to "a@b#c$d" (7 chars) -> fails
    with pytest.raises(ValueError, match="must be at least 16 characters"):
        Settings(
            app_env="production",
            postgres_dsn="postgresql+psycopg://sre_admin:a%40b%23c%24d@pg-prod.internal:5432/ops",
        )

    # 8. Valid standard PostgreSQL DSN with >= 16 char password must succeed
    s_prod = Settings(
        app_env="production",
        postgres_dsn="postgresql+psycopg://sre_admin:StrongPasswordLongSecret99!@pg-cluster.internal:5432/ops",
    )
    assert s_prod.sql_url.startswith("postgresql+psycopg://sre_admin")

    # 9. Valid URL-encoded special characters in password (decoded length >= 16) must succeed
    # "Secret%40Token%23Long123" decodes to "Secret@Token#Long123" (20 chars) -> succeeds
    s_encoded = Settings(
        app_env="production",
        postgres_dsn="postgresql+psycopg://sre_admin:Secret%40Token%23Long123@pg-cluster.internal:5432/ops",
    )
    assert s_encoded.sql_url.startswith("postgresql+psycopg://sre_admin")

    # 10. Valid installed synchronous drivers (postgresql:// normalized to postgresql+psycopg://) must succeed
    s_plain = Settings(
        app_env="production",
        postgres_dsn="postgresql://sre_admin:StrongPasswordLongSecret99!@pg-cluster.internal:5432/ops",
    )
    # Bare postgresql:// is automatically normalized to explicit postgresql+psycopg://
    assert s_plain.sql_url.startswith("postgresql+psycopg://sre_admin")
    from sqlalchemy import create_engine
    assert create_engine(s_plain.sql_url).dialect.driver == "psycopg"

    # Uninstalled synchronous driver (psycopg2) must be rejected
    with pytest.raises(ValueError, match="Supported installed driver is 'postgresql\\+psycopg'"):
        Settings(
            app_env="production",
            postgres_dsn="postgresql+psycopg2://sre_admin:StrongPasswordLongSecret99!@pg-cluster.internal:5432/ops",
        )

    # 11. No double-decoding: literal % encoded as %25 is decoded once, not turned into spaces or control chars
    # Raw password: "Secret%20TokenLong123!" -> encoded in DSN as "Secret%2520TokenLong123!"
    from sqlalchemy.engine.url import make_url
    s_percent = Settings(
        app_env="production",
        postgres_dsn="postgresql+psycopg://sre_admin:Secret%2520TokenLong123!@pg-cluster.internal:5432/ops",
    )
    parsed_pw = make_url(s_percent.postgres_dsn).password
    assert parsed_pw == "Secret%20TokenLong123!"  # exactly 1 decode, not converted to space

    # 12. Valid managed Neon PostgreSQL DSN with SSL query params must succeed
    s_neon = Settings(
        app_env="production",
        postgres_dsn="postgresql+psycopg://sre_admin:npg_SecretTokenLongerThan16Chars987!@ep-cool-pond-12345.us-east-2.aws.neon.tech/neondb?sslmode=require",
    )
    assert "neon.tech" in s_neon.sql_url
