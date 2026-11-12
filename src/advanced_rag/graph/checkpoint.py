"""PostgreSQL Checkpoint & Connection Management for LangGraph.

Stage 6 Tasks 66 & 67:
- Synchronous PostgresSaver integration with psycopg_pool.ConnectionPool.
- Dedicated connection pooling with configurable min/max size and timeouts.
- Bounded retries with exponential backoff on transient connection failures.
- Idempotent schema initialization (saver.setup()) without repeated overhead.
- Safe connection cleanup and lifespan lifecycle management.
- Credential masking: never exposes passwords in logs or exceptions.
- Fails closed without silent in-memory fallback when CHECKPOINT_BACKEND=postgres is set.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from advanced_rag.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Connection pool singleton state
_pool_lock = threading.Lock()
_global_pool: Any = None
_pool_dsn: str | None = None
_setup_completed = False


def _normalize_psycopg_dsn(dsn: str, sslmode: str | None = None) -> str:
    """Normalize SQLAlchemy DSN to standard psycopg libpq connection string.

    Converts:
        postgresql+psycopg://user:pass@host:port/db -> postgresql://user:pass@host:port/db
        postgresql+psycopg2://...                  -> postgresql://...
    """
    clean = dsn.strip()
    if clean.startswith("postgresql+psycopg://"):
        clean = "postgresql://" + clean[len("postgresql+psycopg://") :]
    elif clean.startswith("postgresql+psycopg2://"):
        clean = "postgresql://" + clean[len("postgresql+psycopg2://") :]

    if sslmode:
        sep = "&" if "?" in clean else "?"
        clean = f"{clean}{sep}sslmode={sslmode}"

    return clean


class PostgresPoolManager:
    """Thread-safe lifecycle manager for the PostgreSQL connection pool."""

    @classmethod
    def get_pool(cls, settings: Settings | None = None) -> Any:
        global _global_pool, _pool_dsn
        st = settings or get_settings()
        raw_dsn = st.effective_checkpoint_dsn
        if not raw_dsn:
            raise ValueError(
                "CHECKPOINT_BACKEND=postgres requires POSTGRES_CHECKPOINT_DSN or POSTGRES_DSN to be configured."
            )

        norm_dsn = _normalize_psycopg_dsn(raw_dsn, st.postgres_sslmode)
        masked = st.masked_dsn(raw_dsn)

        with _pool_lock:
            if _global_pool is not None:
                # Return existing pool if DSN hasn't changed
                if _pool_dsn == norm_dsn:
                    return _global_pool
                cls.close_pool()

            try:
                from psycopg_pool import ConnectionPool
            except ImportError as err:
                raise RuntimeError(
                    "psycopg_pool is required for PostgreSQL checkpoint pooling. "
                    "Install with: pip install 'psycopg[pool]'"
                ) from err

            logger.info(
                "Initializing PostgreSQL checkpointer connection pool (min=%d, max=%d, timeout=%.1fs, target=%s)",
                st.postgres_pool_min_size,
                st.postgres_pool_max_size,
                st.postgres_pool_timeout,
                masked,
            )

            # Create pool; open=False lets us test connection with bounded retries before marking ready
            pool = ConnectionPool(
                conninfo=norm_dsn,
                kwargs={"autocommit": True, "prepare_threshold": 0},
                min_size=st.postgres_pool_min_size,
                max_size=st.postgres_pool_max_size,
                timeout=st.postgres_pool_timeout,
                open=False,
            )

            # Bounded retry loop for initial connection
            last_err: Exception | None = None
            max_retries = max(1, st.postgres_max_retries)
            backoff = max(0.1, st.postgres_retry_backoff)

            for attempt in range(1, max_retries + 1):
                try:
                    pool.open()
                    pool.wait(timeout=st.postgres_pool_timeout)
                    # Quick verification query
                    with pool.connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1;")
                    logger.info("PostgreSQL checkpointer connection pool verified successfully.")
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
                    logger.warning(
                        "PostgreSQL connection attempt %d/%d failed for %s (%s). Retrying in %.2fs...",
                        attempt,
                        max_retries,
                        masked,
                        type(exc).__name__,
                        backoff,
                    )
                    try:
                        pool.close()
                    except Exception:
                        pass
                    if attempt < max_retries:
                        time.sleep(backoff)
                        backoff *= 2.0

            if last_err is not None:
                raise RuntimeError(
                    f"Failed to connect to PostgreSQL checkpointer at {masked} "
                    f"after {max_retries} attempts: {type(last_err).__name__}: {last_err}"
                ) from last_err

            _global_pool = pool
            _pool_dsn = norm_dsn
            return _global_pool

    @classmethod
    def close_pool(cls) -> None:
        """Close connection pool and reset global state."""
        global _global_pool, _pool_dsn, _setup_completed
        with _pool_lock:
            if _global_pool is not None:
                try:
                    logger.info("Closing PostgreSQL checkpointer connection pool.")
                    _global_pool.close()
                except Exception as exc:
                    logger.warning("Error closing checkpointer connection pool: %s", exc)
                _global_pool = None
                _pool_dsn = None
            _setup_completed = False


def get_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    """Resolve and return the configured checkpointer.

    Guarantees:
    - If CHECKPOINT_BACKEND=memory: returns MemorySaver().
    - If CHECKPOINT_BACKEND=postgres: returns PostgresSaver backed by ConnectionPool.
    - Idempotently runs saver.setup() once per process.
    - Fails closed on error: NEVER silently falls back to in-memory persistence.
    """
    global _setup_completed
    st = settings or get_settings()

    if st.checkpoint_backend == "memory":
        return MemorySaver()

    if st.checkpoint_backend != "postgres":
        raise ValueError(
            f"Unsupported CHECKPOINT_BACKEND '{st.checkpoint_backend}'. "
            "Supported backends are 'memory' and 'postgres'."
        )

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as err:
        raise RuntimeError(
            "langgraph-checkpoint-postgres is required when CHECKPOINT_BACKEND=postgres. "
            "Install with: pip install 'langgraph-checkpoint-postgres>=2.0'"
        ) from err

    pool = PostgresPoolManager.get_pool(st)
    saver = PostgresSaver(conn=pool)

    with _pool_lock:
        if not _setup_completed:
            try:
                logger.info("Initializing LangGraph PostgreSQL checkpoint schema (saver.setup())...")
                saver.setup()
                _setup_completed = True
                logger.info("LangGraph PostgreSQL checkpoint schema initialized successfully.")
            except Exception as exc:
                masked = st.masked_dsn(st.effective_checkpoint_dsn)
                raise RuntimeError(
                    f"Failed to initialize PostgreSQL checkpoint schema on {masked}: {exc}"
                ) from exc

    return saver


def init_checkpointer(settings: Settings | None = None) -> BaseCheckpointSaver:
    """Warm up and initialize checkpointer during application startup (e.g. FastAPI lifespan)."""
    return get_checkpointer(settings)


def close_checkpointer() -> None:
    """Shut down checkpointer resources and dispose connection pool cleanly."""
    PostgresPoolManager.close_pool()


def reset_checkpointer() -> None:
    """Reset checkpointer state and compiled graph cache for test isolation."""
    from advanced_rag.graph.builder import get_graph

    close_checkpointer()
    try:
        get_graph.cache_clear()
    except Exception:
        pass

