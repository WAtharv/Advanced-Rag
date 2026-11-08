"""Database connection, session management, and schema initialization.

Supports PostgreSQL via psycopg (v3) in production and SQLite for local development/testing.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from advanced_rag.config import Settings, get_settings
from advanced_rag.db.models import Base

logger = logging.getLogger(__name__)

_global_engine: Engine | None = None
_global_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None, force_new: bool = False) -> Engine:
    """Obtain or initialize the global SQLAlchemy Engine."""
    global _global_engine, _global_session_factory
    if _global_engine is not None and not force_new:
        return _global_engine

    st = settings or get_settings()
    url = st.sql_url
    connect_args: dict[str, Any] = {}

    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    logger.debug("Initializing database engine: dialect=%s", st.sql_dialect)
    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if not force_new:
        _global_engine = engine
        _global_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Obtain the sessionmaker bound to the given or default engine."""
    global _global_session_factory
    if engine is None:
        if _global_session_factory is None:
            eng = get_engine()
            _global_session_factory = sessionmaker(bind=eng, expire_on_commit=False)
        return _global_session_factory
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_db_session(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Transactional context manager for database sessions.

    Automatically commits on normal exit and rolls back on exception.
    Always closes the session cleanly upon exit.
    """
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> None:
    """Initialize database schema idempotently across all declared models."""
    eng = engine or get_engine()
    Base.metadata.create_all(bind=eng)
    logger.info("Initialized database schema for models on %s", eng.url.render_as_string(hide_password=True))


def reset_db_engine() -> None:
    """Reset global engine and session factory for test isolation."""
    global _global_engine, _global_session_factory
    if _global_engine is not None:
        try:
            _global_engine.dispose()
        except Exception:
            pass
    _global_engine = None
    _global_session_factory = None

