"""Central configuration.

Every external service is optional: leaving its URL/DSN blank selects a local
fallback (embedded Qdrant, SQLite, in-process cache) so the pipeline runs on a
laptop with no Docker as well.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- Environment ----------
    app_env: Literal["development", "production", "test"] = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT", "app_env", "environment"),
    )

    # ---------- LLM ----------
    #: Which LLM backend the graph/guardrails call through.
    #: "groq" = Hosted high-speed inference via the official Groq SDK (free tier);
    #: "ollama" = Local self-hosted LLM via Ollama HTTP API (zero external calls);
    #: "anthropic" = Claude via the Anthropic SDK;
    #: "gemini" = Google Gemini via the google-genai SDK;
    #: "offline" = no model at all, which makes generation extractive (the
    #: retrieved passages are quoted instead of synthesised). Offline mode is
    #: also selected automatically when the chosen provider has no credentials.
    llm_provider: Literal["groq", "ollama", "offline", "anthropic", "gemini"] = "groq"

    # Groq settings
    groq_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "groq_api_key"),
    )
    groq_model: str = "llama-3.3-70b-versatile"
    groq_fast_model: str = "llama-3.1-8b-instant"

    # Ollama settings
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_seconds: float = 30.0

    anthropic_api_key: str | None = None
    #: Gemini API key (Google AI Studio). Only used when llm_provider == "gemini".
    #: Accepts either GEMINI_API_KEY (the name Google AI Studio hands out) or
    #: GOOGLE_API_KEY; the google-genai SDK itself only reads them from the OS
    #: environment, so loading it here from .env is what actually wires it up.
    google_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "google_api_key"),
    )
    llm_model: str = "llama-3.3-70b-versatile"
    #: Cheaper model for the many small classification/grading calls in the graph.
    llm_fast_model: str = "llama-3.1-8b-instant"
    llm_effort: str = "high"
    llm_max_tokens: int = 16_000
    llm_refusal_fallbacks: bool = True
    #: Request timeout for LLM provider calls.
    llm_timeout_seconds: float = 30.0
    #: Bounded retry for transient 429/5xx errors.
    #: 1 disables retrying.
    llm_max_retries: int = 2
    #: If the primary model still fails with a transient error after retries, try
    #: once on this model. Empty disables it. Point it at the fast model to keep
    #: interactive latency bounded when the main model is being load-shed.
    llm_retry_fallback_model: str | None = None
    #: Input token ceiling to guard against runaway context prompts.
    llm_max_input_tokens: int = 8_000
    #: Maximum LLM calls allowed across a single user request.
    llm_max_calls_per_request: int = 10

    # ---------- Vector store ----------
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_path: Path = Path("data/qdrant")
    qdrant_collection: str = "sre_copilot_v1"
    #: Require explicit enablement before falling back to the baseline k8s_ops collection.
    allow_collection_fallback: bool = False

    # ---------- Embeddings / reranking ----------
    #: "fastembed" = dense + sparse + cross-encoder (downloads ONNX models).
    #: "keyword"   = pure-Python BM25 only; no download, no dense arm, no reranker.
    #: "gemini"    = dense via the Gemini embeddings API + sparse via local BM25.
    #:              The on-network path to a real dense arm where huggingface.co
    #:              is blocked but the Gemini API is reachable. No cross-encoder
    #:              (that stays the lexical stand-in), no ONNX download.
    #: "auto"      = try fastembed, fall back to keyword if the models cannot be got.
    retrieval_backend: Literal["auto", "fastembed", "keyword", "gemini"] = "fastembed"
    dense_model: str = "BAAI/bge-small-en-v1.5"
    sparse_model: str = "local-bm25"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    #: Gemini embedding model + output width, used only when retrieval_backend ==
    #: "gemini". gemini-embedding-001 emits 3072 dims by default but supports
    #: Matryoshka truncation; 768 keeps the Qdrant collection lean at negligible
    #: quality cost. Reuses google_api_key (GEMINI_API_KEY).
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 384
    #: Where fastembed keeps downloaded ONNX models. Point this at a pre-populated
    #: directory on a network that blocks huggingface.co.
    model_cache_dir: Path | None = None
    #: Hugging Face token, used only to authenticate model downloads. Read from
    #: .env (gitignored) and exported to HF_TOKEN for huggingface_hub.
    hf_token: str | None = None

    # ---------- Text2SQL store ----------
    postgres_dsn: str | None = None
    postgres_readonly_dsn: str | None = None
    sqlite_path: Path = Path("data/ops.db")
    sql_row_limit: int = 200
    sql_timeout_seconds: float = 15.0
    sql_max_result_cells: int = 5000
    sql_max_concurrent_queries: int = 5
    approval_expiry_seconds: int = 300

    # ---------- Graph state ----------
    #: "memory" keeps SQL-approval run state in-process (single replica / sticky).
    #: "postgres" persists it via langgraph-checkpoint-postgres so /approve can
    #: land on any replica - requires POSTGRES_DSN and the [postgres-checkpoint]
    #: extra; fails closed if misconfigured.
    checkpoint_backend: Literal["memory", "postgres"] = "memory"

    # ---------- PostgreSQL Checkpoint & Connection Pooling (Stage 6 Tasks 66 & 67) ----------
    postgres_checkpoint_dsn: str | None = Field(
        None,
        validation_alias=AliasChoices("POSTGRES_CHECKPOINT_DSN", "postgres_checkpoint_dsn"),
    )
    postgres_metadata_dsn: str | None = Field(
        None,
        validation_alias=AliasChoices("POSTGRES_METADATA_DSN", "postgres_metadata_dsn"),
    )
    postgres_pool_min_size: int = Field(
        1,
        validation_alias=AliasChoices("POSTGRES_POOL_MIN_SIZE", "postgres_pool_min_size"),
    )
    postgres_pool_max_size: int = Field(
        10,
        validation_alias=AliasChoices("POSTGRES_POOL_MAX_SIZE", "postgres_pool_max_size"),
    )
    postgres_pool_timeout: float = Field(
        30.0,
        validation_alias=AliasChoices("POSTGRES_POOL_TIMEOUT", "postgres_pool_timeout"),
    )
    postgres_max_retries: int = Field(
        3,
        validation_alias=AliasChoices("POSTGRES_MAX_RETRIES", "postgres_max_retries"),
    )
    postgres_retry_backoff: float = Field(
        0.5,
        validation_alias=AliasChoices("POSTGRES_RETRY_BACKOFF", "postgres_retry_backoff"),
    )
    postgres_sslmode: str | None = Field(
        None,
        validation_alias=AliasChoices("POSTGRES_SSLMODE", "postgres_sslmode"),
    )

    # ---------- Cache ----------
    cache_backend: Literal["memory", "redis"] = Field(
        "memory",
        validation_alias=AliasChoices("CACHE_BACKEND", "cache_backend"),
    )
    redis_url: str | None = Field(
        None,
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
    )
    redis_connect_timeout: float = Field(
        2.0,
        validation_alias=AliasChoices("REDIS_CONNECT_TIMEOUT", "redis_connect_timeout"),
    )
    redis_socket_timeout: float = Field(
        2.0,
        validation_alias=AliasChoices("REDIS_SOCKET_TIMEOUT", "redis_socket_timeout"),
    )
    redis_max_payload_bytes: int = Field(
        1_048_576,  # 1 MB
        validation_alias=AliasChoices("REDIS_MAX_PAYLOAD_BYTES", "redis_max_payload_bytes"),
    )
    redis_key_prefix: str = Field(
        "rag:v1",
        validation_alias=AliasChoices("REDIS_KEY_PREFIX", "redis_key_prefix"),
    )
    cache_ttl_seconds: int = 3600
    cache_default_ttl: int = Field(
        3600,
        validation_alias=AliasChoices(
            "CACHE_DEFAULT_TTL", "cache_default_ttl", "CACHE_TTL_SECONDS", "cache_ttl_seconds"
        ),
    )
    cache_max_entries: int = Field(
        1000,
        validation_alias=AliasChoices("CACHE_MAX_ENTRIES", "cache_max_entries"),
    )
    semantic_cache_threshold: float = 0.95

    # Intelligent cache tiers (Stage 6 Tasks 64 & 65)
    embedding_cache_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("EMBEDDING_CACHE_ENABLED", "embedding_cache_enabled"),
    )
    embedding_cache_ttl: int = Field(
        86400,
        validation_alias=AliasChoices("EMBEDDING_CACHE_TTL", "embedding_cache_ttl"),
    )
    embedding_cache_max_entries: int = Field(
        2000,
        validation_alias=AliasChoices("EMBEDDING_CACHE_MAX_ENTRIES", "embedding_cache_max_entries"),
    )

    intent_cache_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("INTENT_CACHE_ENABLED", "intent_cache_enabled"),
    )
    intent_cache_ttl: int = Field(
        3600,
        validation_alias=AliasChoices("INTENT_CACHE_TTL", "intent_cache_ttl"),
    )
    intent_cache_max_entries: int = Field(
        1000,
        validation_alias=AliasChoices("INTENT_CACHE_MAX_ENTRIES", "intent_cache_max_entries"),
    )

    retrieval_cache_enabled: bool = Field(
        False,
        validation_alias=AliasChoices("RETRIEVAL_CACHE_ENABLED", "retrieval_cache_enabled"),
    )
    retrieval_cache_ttl: int = Field(
        600,
        validation_alias=AliasChoices("RETRIEVAL_CACHE_TTL", "retrieval_cache_ttl"),
    )
    retrieval_cache_max_entries: int = Field(
        500,
        validation_alias=AliasChoices("RETRIEVAL_CACHE_MAX_ENTRIES", "retrieval_cache_max_entries"),
    )

    # ---------- Retrieval tuning ----------
    chunk_size: int = 900
    chunk_overlap: int = 150
    retrieve_top_k: int = 20
    rerank_top_n: int = 5
    hybrid_dense_weight: float = Field(0.7, ge=0.0, le=1.0)
    hybrid_fusion: Literal["weighted", "rrf"] = "rrf"
    hybrid_prefetch_limit: int | None = Field(
        None,
        description="Candidate prefetch depth for server-side RRF fusion. If None, dynamically defaults to max(top_k * 2, top_k + 20).",
    )
    enable_rerank: bool = False
    #: CRAG: below this reranker score the context is considered insufficient.
    crag_relevance_floor: float = 0.25
    #: Self-RAG: max answer regeneration attempts.
    self_rag_max_retries: int = 1
    #: Context selection & budget
    max_context_tokens: int = Field(2500, ge=200, le=8000, description="Max token budget for retrieved context supplied to LLM")
    enable_context_deduplication: bool = Field(True, description="Filter redundant and highly overlapping adjacent chunks in LLM context")
    min_context_score_ratio: float = Field(0.20, ge=0.0, le=1.0, description="Filter low-scoring noise chunks relative to top chunk score")

    # ---------- Feature flags ----------
    enable_hyde: bool = True
    enable_crag: bool = True
    enable_self_rag: bool = False
    enable_text2sql: bool = True
    enable_guardrails: bool = True
    #: Granular switch: enable LLM-based intent & scope verification for inbound questions.
    #: If false, deterministic input layers (shape, PII redaction, secret request, injection) remain active.
    enable_guardrails_llm_input: bool = True
    #: Granular switch: enable LLM-based safety review for outbound answers.
    #: If false, deterministic output layers (destructive command notice, secret egress redaction) remain active.
    enable_guardrails_llm_output: bool = True
    enable_cache: bool = True

    # ---------- External Search Fallback (Task 49) ----------
    #: Fall back to external web search when internal RAG context is insufficient.
    enable_external_search: bool = False
    #: Search provider backend: "searxng" (preferred, self-hosted) or "tavily" (optional).
    external_search_provider: str = "searxng"
    #: Base URL for SearXNG instance (e.g. "http://localhost:8080" or "https://searxng.internal").
    searxng_base_url: str = ""
    #: Request timeout in seconds for external search calls.
    searxng_timeout_seconds: float = 5.0
    #: Maximum external search results to consider and format into context.
    external_search_max_results: int = 5
    #: Optional Tavily API key for Tavily search provider.
    tavily_api_key: str | None = None
    #: Explicit toggle required to enable Tavily provider.
    external_search_allow_tavily: bool = False

    # ---------- API ----------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    @property
    def use_remote_qdrant(self) -> bool:
        return bool(self.qdrant_url)

    @property
    def sql_url(self) -> str:
        """SQLAlchemy URL for the Text2SQL store, with a SQLite fallback."""
        if self.postgres_dsn:
            dsn = self.postgres_dsn
            # Normalize bare postgresql:// to explicit postgresql+psycopg:// to guarantee
            # the installed modern psycopg (v3) synchronous DBAPI driver is always loaded.
            if dsn.startswith("postgresql://"):
                return "postgresql+psycopg://" + dsn[len("postgresql://"):]
            return dsn
        path = self.absolute(self.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{path.as_posix()}"

    @property
    def sql_readonly_url(self) -> str:
        """SQLAlchemy URL for Text2SQL read-only execution.

        Fails closed if PostgreSQL is configured without a dedicated read-only DSN.
        """
        if self.postgres_readonly_dsn:
            dsn = self.postgres_readonly_dsn
            if dsn.startswith("postgresql://"):
                return "postgresql+psycopg://" + dsn[len("postgresql://"):]
            return dsn
        if self.postgres_dsn:
            raise ValueError(
                "POSTGRES_READONLY_DSN must be explicitly configured when PostgreSQL is used for Text2SQL. "
                "Falling back to application write credentials (POSTGRES_DSN) is strictly forbidden."
            )
        path = self.absolute(self.sqlite_path)
        return f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true"

    @property
    def metadata_sql_url(self) -> str:
        """SQLAlchemy URL for application metadata (document_metadata, ingestion_runs, approval_audit).

        If POSTGRES_METADATA_DSN is configured, uses PostgreSQL via modern psycopg driver.
        When absent, preserves the existing SQLite fallback behavior (data/ops.db).
        Does NOT silently fall back to POSTGRES_CHECKPOINT_DSN.
        """
        if self.postgres_metadata_dsn:
            dsn = self.postgres_metadata_dsn.strip()
            if dsn:
                if dsn.startswith("postgresql://"):
                    return "postgresql+psycopg://" + dsn[len("postgresql://") :]
                return dsn
        path = self.absolute(self.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{path.as_posix()}"

    @property
    def effective_checkpoint_dsn(self) -> str | None:
        """DSN to use for PostgreSQL LangGraph checkpoints.

        Prefers POSTGRES_CHECKPOINT_DSN if explicitly configured, else falls back to POSTGRES_DSN.
        """
        return self.postgres_checkpoint_dsn or self.postgres_dsn

    def masked_dsn(self, dsn: str | None) -> str:
        """Safely redacts passwords and credentials from database connection strings."""
        if not dsn:
            return ""
        try:
            from sqlalchemy.engine import make_url

            url = make_url(dsn)
            return url.render_as_string(hide_password=True)
        except Exception:
            return "<configured-dsn>"

    @property
    def sql_dialect(self) -> str:
        return "PostgreSQL" if self.postgres_dsn else "SQLite"

    @model_validator(mode="after")
    def _validate_production_database(self) -> Settings:
        """Enforce strict database security rules when running in production mode."""
        if self.app_env != "production":
            return self

        if not self.postgres_dsn:
            raise ValueError(
                "POSTGRES_DSN must be explicitly configured when APP_ENV=production. "
                "SQLite fallback is forbidden in production."
            )

        # Normalize bare postgresql:// to explicit postgresql+psycopg://
        if self.postgres_dsn.startswith("postgresql://"):
            self.postgres_dsn = "postgresql+psycopg://" + self.postgres_dsn[len("postgresql://"):]

        from sqlalchemy.engine.url import make_url
        from sqlalchemy.exc import ArgumentError

        try:
            url = make_url(self.postgres_dsn)
        except ArgumentError as exc:
            raise ValueError(
                "POSTGRES_DSN is not a valid database connection string."
            ) from exc

        # 1. Require installed synchronous PostgreSQL driver (reject asyncpg/aiopg, uninstalled drivers, and SQLite)
        supported_sync_drivers = {
            "postgresql",
            "postgresql+psycopg",
        }
        if url.drivername not in supported_sync_drivers:
            if "async" in url.drivername or "aio" in url.drivername:
                raise ValueError(
                    f"Asynchronous PostgreSQL driver '{url.drivername}' is not supported. "
                    "The application uses synchronous create_engine; please use 'postgresql+psycopg' or 'postgresql'."
                )
            raise ValueError(
                f"Production requires a supported synchronous PostgreSQL driver (got '{url.drivername}'). "
                "Supported installed driver is 'postgresql+psycopg' (bare 'postgresql' is normalized to it). "
                "SQLite, uninstalled drivers (psycopg2/pg8000), and non-PostgreSQL drivers are forbidden in production."
            )

        # 2. Hostname check
        if not url.host:
            raise ValueError(
                "Production POSTGRES_DSN must include a valid hostname."
            )

        # 3. Database name check
        if not url.database:
            raise ValueError(
                "Production POSTGRES_DSN must specify a database name."
            )

        # 4. Username check
        if not url.username:
            raise ValueError(
                "Production POSTGRES_DSN must include a database username."
            )

        # 5. Non-empty password check
        if not url.password:
            raise ValueError(
                "Production POSTGRES_DSN must include a non-empty password."
            )

        # SQLAlchemy make_url() already decodes URL-encoded credentials (e.g. %40 -> @).
        # url.password is the exact string passed to the DBAPI driver; do not call unquote()
        # again to prevent accidental double-decoding (e.g. %2520 -> %20, not a space).
        password = url.password

        # 6. Reject known weak default and placeholder passwords
        insecure_placeholders = {
            "postgres",
            "password",
            "changeme",
            "your_password",
            "your_strong_password_here",
            "admin",
            "root",
            "secret",
            "123456",
            "12345678",
            "1234567890123456",
        }
        if password.strip().lower() in insecure_placeholders:
            raise ValueError(
                "Production database password cannot be a known default or placeholder password. "
                "Configure a strong, unique secret."
            )

        # 7. Minimum password length check (>= 16 characters)
        min_length = 16
        if len(password) < min_length:
            raise ValueError(
                f"Production database password must be at least {min_length} characters long."
            )

        return self

    def absolute(self, path: Path) -> Path:
        """Resolve a configured path relative to the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
