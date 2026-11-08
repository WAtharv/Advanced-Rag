"""Persistent document metadata registry backed by PostgreSQL (with SQLite fallback).

Implements Task 43:
- Full lifecycle status tracking: pending, indexing, indexed, failed, deletion_pending, deleted
- Idempotent document registration and metadata updates
- Ingestion run auditing and history
- Qdrant-to-registry consistency comparison
- Built-in corpus seeding and discovery
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from advanced_rag.config import Settings, get_settings
from advanced_rag.db.models import DocumentMetadataRecord, IngestionRunRecord
from advanced_rag.db.session import get_db_session, get_engine, init_db
from advanced_rag.ingestion.loader import CORPUS_DIR, load_path
from advanced_rag.retrieval.vectorstore import HybridStore

logger = logging.getLogger(__name__)


class PersistentDocumentRegistry:
    """Thread-safe persistent document metadata registry."""

    def __init__(
        self,
        engine: Engine | None = None,
        settings: Settings | None = None,
        auto_init: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine or get_engine(self.settings)
        if auto_init:
            init_db(self.engine)

    def register(self, record: dict[str, Any]) -> dict[str, Any]:
        """Register or update a document record idempotently."""
        doc_id = record.get("document_id")
        source = record.get("source", "")
        if not doc_id:
            doc_id = hashlib.sha256(source.encode()).hexdigest()[:16] if source else uuid.uuid4().hex[:16]

        col_name = record.get("collection_name") or self.settings.qdrant_collection
        title = record.get("title", "")
        f_type = record.get("file_type") or Path(source).suffix.lstrip(".").lower() or "text"
        c_hash = record.get("content_hash", "")
        category = record.get("category") or (source.split("/")[0] if "/" in source else None)
        component = record.get("component")
        doc_type = record.get("doc_type", "runbook")
        status = record.get("status") or record.get("document_status", "pending")
        chunk_count = record.get("chunk_count") or record.get("indexed_chunk_count", 0)

        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(
                (DocumentMetadataRecord.document_id == doc_id)
                | (
                    (DocumentMetadataRecord.source == source)
                    & (DocumentMetadataRecord.collection_name == col_name)
                )
            )
            existing = session.scalars(stmt).first()

            if existing:
                existing.document_id = doc_id
                existing.source = source
                if title:
                    existing.title = title
                if f_type:
                    existing.file_type = f_type
                if c_hash:
                    existing.content_hash = c_hash
                if category:
                    existing.category = category
                if component:
                    existing.component = component
                if doc_type:
                    existing.doc_type = doc_type
                if record.get("source_url"):
                    existing.source_url = record["source_url"]
                if record.get("source_repository"):
                    existing.source_repository = record["source_repository"]
                if record.get("source_version"):
                    existing.source_version = record["source_version"]
                if record.get("source_commit"):
                    existing.source_commit = record["source_commit"]
                if record.get("spdx_license"):
                    existing.spdx_license = record["spdx_license"]
                if record.get("attribution"):
                    existing.attribution = record["attribution"]
                existing.document_status = status
                existing.indexed_chunk_count = chunk_count
                existing.collection_name = col_name
                existing.updated_at = datetime.now(timezone.utc)
                if status == "indexed":
                    existing.last_indexed_at = datetime.now(timezone.utc)
                    existing.last_error = None
                elif status == "failed" and record.get("last_error"):
                    existing.last_error = record["last_error"]
                return existing.to_dict()

            # Insert new record
            new_record = DocumentMetadataRecord(
                document_id=doc_id,
                source=source,
                title=title,
                file_type=f_type,
                content_hash=c_hash,
                category=category,
                component=component,
                doc_type=doc_type,
                source_url=record.get("source_url"),
                source_repository=record.get("source_repository"),
                source_version=record.get("source_version"),
                source_commit=record.get("source_commit"),
                spdx_license=record.get("spdx_license"),
                attribution=record.get("attribution"),
                document_status=status,
                indexed_chunk_count=chunk_count,
                collection_name=col_name,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                last_indexed_at=datetime.now(timezone.utc) if status == "indexed" else None,
                last_error=record.get("last_error"),
            )
            session.add(new_record)
            session.flush()
            return new_record.to_dict()

    def get(self, document_id: str) -> dict[str, Any] | None:
        """Retrieve document metadata by document_id with stem fallback."""
        if not document_id:
            return None

        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(DocumentMetadataRecord.document_id == document_id)
            rec = session.scalars(stmt).first()
            if rec:
                return rec.to_dict()

            # Fallback check: match by stem or prefix
            clean_id = document_id.lower().replace("builtin_", "")
            stmt_stem = select(DocumentMetadataRecord).where(
                DocumentMetadataRecord.document_id.like(f"%{clean_id}%")
            )
            rec_stem = session.scalars(stmt_stem).first()
            if rec_stem:
                return rec_stem.to_dict()
            return None

    def get_by_source(self, source: str, collection_name: str | None = None) -> dict[str, Any] | None:
        """Retrieve document metadata by source path."""
        norm_source = Path(source).as_posix()
        col = collection_name or self.settings.qdrant_collection

        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(
                (DocumentMetadataRecord.source == norm_source)
                & (DocumentMetadataRecord.collection_name == col)
            )
            rec = session.scalars(stmt).first()
            if rec:
                return rec.to_dict()

            # Try by raw source
            stmt2 = select(DocumentMetadataRecord).where(DocumentMetadataRecord.source == source)
            rec2 = session.scalars(stmt2).first()
            return rec2.to_dict() if rec2 else None

    def update_status(
        self,
        document_id: str,
        status: str,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> bool:
        """Update operational status and chunk counts for a document."""
        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(DocumentMetadataRecord.document_id == document_id)
            rec = session.scalars(stmt).first()
            if not rec:
                return False

            rec.document_status = status
            rec.updated_at = datetime.now(timezone.utc)
            if chunk_count is not None:
                rec.indexed_chunk_count = chunk_count
            if status == "indexed":
                rec.last_indexed_at = datetime.now(timezone.utc)
                rec.last_error = None
            elif status == "failed":
                rec.last_error = error
            return True

    def record_indexing_success(
        self,
        document_id: str,
        chunk_count: int,
        content_hash: str | None = None,
    ) -> bool:
        """Mark document indexing as successful."""
        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(DocumentMetadataRecord.document_id == document_id)
            rec = session.scalars(stmt).first()
            if not rec:
                return False

            rec.document_status = "indexed"
            rec.indexed_chunk_count = chunk_count
            rec.last_indexed_at = datetime.now(timezone.utc)
            rec.last_error = None
            rec.updated_at = datetime.now(timezone.utc)
            if content_hash:
                rec.content_hash = content_hash
            return True

    def record_indexing_failure(self, document_id: str, error: str) -> bool:
        """Record document indexing failure with details."""
        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(DocumentMetadataRecord.document_id == document_id)
            rec = session.scalars(stmt).first()
            if not rec:
                return False

            rec.document_status = "failed"
            rec.last_error = error
            rec.updated_at = datetime.now(timezone.utc)
            return True

    def delete(self, document_id: str) -> bool:
        """Mark a document as deleted."""
        with get_db_session(self.engine) as session:
            stmt = select(DocumentMetadataRecord).where(DocumentMetadataRecord.document_id == document_id)
            rec = session.scalars(stmt).first()
            if not rec:
                return False

            rec.document_status = "deleted"
            rec.indexed_chunk_count = 0
            rec.updated_at = datetime.now(timezone.utc)
            return True

    def list_documents(
        self,
        category: str | None = None,
        status: str | None = None,
        component: str | None = None,
        file_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Query document metadata with filtering and pagination."""
        with get_db_session(self.engine) as session:
            query = select(DocumentMetadataRecord)
            count_query = select(func.count(DocumentMetadataRecord.id))

            filters = []
            if not include_deleted:
                filters.append(DocumentMetadataRecord.document_status != "deleted")
            if category:
                filters.append(DocumentMetadataRecord.category == category)
            if status:
                filters.append(DocumentMetadataRecord.document_status == status)
            if component:
                filters.append(DocumentMetadataRecord.component == component)
            if file_type:
                filters.append(DocumentMetadataRecord.file_type == file_type)

            if filters:
                query = query.where(*filters)
                count_query = count_query.where(*filters)

            total_count = session.scalar(count_query) or 0
            records = (
                session.scalars(
                    query.order_by(DocumentMetadataRecord.source.asc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return [r.to_dict() for r in records], total_count

    def record_ingestion_run(
        self,
        operation_type: str,
        collection_name: str,
        status: str = "in_progress",
        run_id: str | None = None,
        details: str | None = None,
    ) -> str:
        """Create an ingestion audit run record."""
        active_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        with get_db_session(self.engine) as session:
            run = IngestionRunRecord(
                run_id=active_run_id,
                operation_type=operation_type,
                status=status,
                collection_name=collection_name,
                started_at=datetime.now(timezone.utc),
                details=details,
            )
            session.add(run)
        return active_run_id

    def complete_ingestion_run(
        self,
        run_id: str,
        status: str,
        successful_files: int,
        failed_files: int,
        total_chunks: int,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Mark an ingestion run as complete with metrics."""
        now = datetime.now(timezone.utc)
        with get_db_session(self.engine) as session:
            stmt = select(IngestionRunRecord).where(IngestionRunRecord.run_id == run_id)
            run = session.scalars(stmt).first()
            if not run:
                return

            run.status = status
            run.completed_at = now
            if run.started_at:
                st_at = run.started_at
                if st_at.tzinfo is None:
                    st_at = st_at.replace(tzinfo=timezone.utc)
                delta = now - st_at
                run.duration_seconds = max(0.0, delta.total_seconds())
            run.successful_files = successful_files
            run.failed_files = failed_files
            run.total_chunks = total_chunks
            run.error_message = error
            if details:
                run.details = json.dumps(details)

    def get_latest_ingestion_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent ingestion runs."""
        with get_db_session(self.engine) as session:
            stmt = (
                select(IngestionRunRecord)
                .order_by(IngestionRunRecord.started_at.desc())
                .limit(limit)
            )
            runs = session.scalars(stmt).all()
            return [r.to_dict() for r in runs]

    def seed_built_in_documents(
        self,
        corpus_dir: Path | str = CORPUS_DIR,
        collection_name: str = "sre_copilot_v1",
    ) -> int:
        """Seed registry with built-in corpus documents and their rich metadata."""
        target = Path(corpus_dir)
        if not target.is_absolute():
            target = self.settings.absolute(target)

        if not target.exists():
            return 0

        docs = load_path(target, ignore_errors=True)
        count = 0
        for doc in docs:
            norm_source = Path(doc.source).as_posix()
            doc_id = (
                doc.metadata.get("document_id")
                or hashlib.sha256(norm_source.encode()).hexdigest()[:16]
            )
            c_hash = hashlib.sha256(doc.body.encode()).hexdigest()
            f_type = doc.file_type or Path(norm_source).suffix.lstrip(".").lower() or "md"
            cat = doc.category or (norm_source.split("/")[0] if "/" in norm_source else None)

            record_dict = {
                "document_id": doc_id,
                "source": norm_source,
                "title": doc.title,
                "file_type": f_type,
                "content_hash": c_hash,
                "category": cat,
                "component": doc.component or doc.metadata.get("component"),
                "doc_type": doc.doc_type or "runbook",
                "source_url": doc.source_url or doc.metadata.get("source_url"),
                "source_repository": doc.metadata.get("source_repository"),
                "source_version": doc.source_version or doc.metadata.get("source_version"),
                "source_commit": doc.metadata.get("source_commit"),
                "spdx_license": doc.metadata.get("spdx_license"),
                "attribution": doc.metadata.get("attribution"),
                "status": "indexed",
                "collection_name": collection_name,
            }
            self.register(record_dict)
            count += 1
        logger.info("Seeded %d documents into persistent registry for '%s'", count, collection_name)
        return count

    def compare_with_qdrant(self, store: HybridStore) -> dict[str, Any]:
        """Compare registered metadata with live Qdrant points."""
        indexed_qdrant = store.get_indexed_documents()
        docs, _ = self.list_documents(limit=10000, include_deleted=True)

        db_by_source = {d["source"]: d for d in docs}
        qdrant_sources = set(indexed_qdrant.keys())
        db_sources = set(db_by_source.keys())

        consistent = []
        chunk_mismatch = []
        registry_only = []
        qdrant_only = []

        for src in db_sources.intersection(qdrant_sources):
            db_doc = db_by_source[src]
            q_info = indexed_qdrant[src]
            q_chunks = q_info.get("chunk_count", 0)
            db_chunks = db_doc.get("chunk_count", 0)
            if db_doc["status"] == "indexed" and (db_chunks == 0 or db_chunks == q_chunks):
                consistent.append(src)
            else:
                chunk_mismatch.append({
                    "source": src,
                    "registry_chunks": db_chunks,
                    "qdrant_chunks": q_chunks,
                    "registry_status": db_doc["status"],
                })

        for src in db_sources - qdrant_sources:
            if db_by_source[src]["status"] != "deleted":
                registry_only.append(src)

        for src in qdrant_sources - db_sources:
            qdrant_only.append(src)

        return {
            "consistent_count": len(consistent),
            "chunk_mismatch_count": len(chunk_mismatch),
            "registry_only_count": len(registry_only),
            "qdrant_only_count": len(qdrant_only),
            "consistent": consistent,
            "chunk_mismatch": chunk_mismatch,
            "registry_only": registry_only,
            "qdrant_only": qdrant_only,
        }

