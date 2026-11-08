"""SQLAlchemy declarative models for document metadata registry and ingestion tracking.

Provides portable schema definitions compatible with both PostgreSQL and SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all persistent database models."""

    pass


class DocumentMetadataRecord(Base):
    """Persistent metadata record for a document in the knowledge base."""

    __tablename__ = "document_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    file_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    component: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False, default="runbook")
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_repository: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spdx_license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attribution: Mapped[str | None] = mapped_column(String(256), nullable=True)
    document_status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        nullable=False,
        default="pending",
    )  # pending, indexing, indexed, failed, deletion_pending, deleted
    indexed_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collection_name: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
        default="sre_copilot_v1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_doc_cat_status", "category", "document_status"),
        Index("idx_doc_source_col", "source", "collection_name"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert record to a serializable dictionary matching registry interface."""
        return {
            "document_id": self.document_id,
            "source": self.source,
            "title": self.title,
            "file_type": self.file_type,
            "content_hash": self.content_hash,
            "category": self.category,
            "component": self.component,
            "doc_type": self.doc_type,
            "source_url": self.source_url,
            "source_repository": self.source_repository,
            "source_version": self.source_version,
            "source_commit": self.source_commit,
            "spdx_license": self.spdx_license,
            "attribution": self.attribution,
            "status": self.document_status,
            "document_status": self.document_status,
            "chunk_count": self.indexed_chunk_count,
            "indexed_chunk_count": self.indexed_chunk_count,
            "collection_name": self.collection_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_indexed_at": self.last_indexed_at.isoformat() if self.last_indexed_at else None,
            "last_error": self.last_error,
        }


class IngestionRunRecord(Base):
    """Audit log record for corpus ingestion, synchronization, and deletion operations."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)  # ingest, sync, delete, reindex
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        nullable=False,
        default="in_progress",
    )  # in_progress, success, failed
    collection_name: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    successful_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        """Convert run record to dictionary."""
        return {
            "run_id": self.run_id,
            "operation_type": self.operation_type,
            "status": self.status,
            "collection_name": self.collection_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "successful_files": self.successful_files,
            "failed_files": self.failed_files,
            "total_chunks": self.total_chunks,
            "details": self.details,
            "error_message": self.error_message,
        }

