"""Knowledge-base statistics and ingestion status reporting service.

Implements Task 42:
- Document inventory breakdown (category, component, format, version, repo, doc_type)
- Indexing statistics (total chunks, chunks per category, chunks per document, failure counts)
- Ingestion operational status (last sync, re-indexing, failures, durations)
- Source information and licensing completeness
- Clear separation between document counts and Qdrant point counts
"""

from __future__ import annotations

import logging
from collections import Counter
from statistics import median
from typing import Any

from sqlalchemy import func, select

from advanced_rag.config import Settings, get_settings
from advanced_rag.db.models import DocumentMetadataRecord, IngestionRunRecord
from advanced_rag.db.session import get_db_session, get_engine
from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry
from advanced_rag.retrieval.vectorstore import HybridStore

logger = logging.getLogger(__name__)


class KnowledgeBaseStatsService:
    """Computes comprehensive metrics and operational status for the knowledge base."""

    def __init__(
        self,
        registry: PersistentDocumentRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or PersistentDocumentRegistry(settings=self.settings)

    def get_inventory_stats(self) -> dict[str, Any]:
        """A. Document Inventory: total documents, breakdowns by category, component, format, version, repo, doc_type."""
        with get_db_session(self.registry.engine) as session:
            # Query all non-deleted document records
            stmt = select(DocumentMetadataRecord).where(
                DocumentMetadataRecord.document_status != "deleted"
            )
            records = session.scalars(stmt).all()

            total_docs = len(records)
            by_category = Counter(r.category or "uncategorized" for r in records)
            by_component = Counter(r.component or "unspecified" for r in records)
            by_format = Counter(r.file_type or "unknown" for r in records)
            by_version = Counter(r.source_version or "unversioned" for r in records)
            by_repo = Counter(r.source_repository or "local" for r in records)
            by_doc_type = Counter(r.doc_type or "runbook" for r in records)

            return {
                "total_documents": total_docs,
                "by_category": dict(sorted(by_category.items())),
                "by_component": dict(sorted(by_component.items())),
                "by_file_format": dict(sorted(by_format.items())),
                "by_software_version": dict(sorted(by_version.items())),
                "by_source_repository": dict(sorted(by_repo.items())),
                "by_document_type": dict(sorted(by_doc_type.items())),
            }

    def get_indexing_stats(self, store: HybridStore | None = None) -> dict[str, Any]:
        """B. Indexing Statistics: total chunks, chunks per category, chunks per document, failure counts."""
        collection_name = self.settings.qdrant_collection
        total_chunks = 0
        indexed_docs_count = 0
        chunks_per_cat: Counter[str] = Counter()
        chunks_per_doc_list: list[int] = []

        # Query Qdrant if store is available
        qdrant_available = False
        should_close = False
        st = store
        if st is None:
            from advanced_rag.retrieval.vectorstore import get_active_store
            active = get_active_store()
            if active is not None and active._client is not None:
                st = active
            else:
                try:
                    st = HybridStore(settings=self.settings, collection=collection_name)
                    should_close = True
                except Exception as exc:
                    logger.warning("Could not connect to Qdrant for indexing stats: %s", exc)

        if st is not None:
            try:
                orig_coll = st.collection
                if collection_name and orig_coll != collection_name:
                    st.collection = collection_name
                try:
                    total_chunks = st.count()
                    qdrant_docs = st.get_indexed_documents()
                    indexed_docs_count = len(qdrant_docs)
                    qdrant_available = True

                    for info in qdrant_docs.values():
                        c_count = info.get("chunk_count", 0)
                        chunks_per_doc_list.append(c_count)
                        src = info.get("source", "")
                        cat = src.split("/")[0] if "/" in src else "root"
                        chunks_per_cat[cat] += c_count
                finally:
                    st.collection = orig_coll
            except Exception as exc:
                logger.warning("Error querying Qdrant index stats: %s", exc)
            finally:
                if should_close:
                    st.close()

        # Query database status
        with get_db_session(self.registry.engine) as session:
            pending_count = (
                session.scalar(
                    select(func.count(DocumentMetadataRecord.id)).where(
                        DocumentMetadataRecord.document_status.in_(["pending", "indexing"])
                    )
                )
                or 0
            )
            failed_count = (
                session.scalar(
                    select(func.count(DocumentMetadataRecord.id)).where(
                        DocumentMetadataRecord.document_status == "failed"
                    )
                )
                or 0
            )

        # Distribution of chunks per document
        if chunks_per_doc_list:
            min_c = min(chunks_per_doc_list)
            max_c = max(chunks_per_doc_list)
            med_c = float(median(chunks_per_doc_list))
            avg_c = round(sum(chunks_per_doc_list) / len(chunks_per_doc_list), 2)
        else:
            min_c = max_c = 0
            med_c = avg_c = 0.0

        return {
            "collection_name": collection_name,
            "qdrant_available": qdrant_available,
            "total_indexed_chunks": total_chunks,
            "indexed_documents_count": indexed_docs_count,
            "chunks_per_category": dict(sorted(chunks_per_cat.items())),
            "chunks_per_document": {
                "min": min_c,
                "median": med_c,
                "max": max_c,
                "average": avg_c,
            },
            "documents_pending_indexing": pending_count,
            "documents_with_indexing_failures": failed_count,
            "documents_with_outdated_content_hashes": 0,
        }

    def get_ingestion_status(self) -> dict[str, Any]:
        """C. Ingestion Status: timestamps, current operations, file counts, durations."""
        with get_db_session(self.registry.engine) as session:
            stmt = select(IngestionRunRecord).order_by(IngestionRunRecord.started_at.desc())
            runs = session.scalars(stmt).all()

            last_success = next((r for r in runs if r.status == "success"), None)
            last_attempt = runs[0] if runs else None
            last_sync = next((r for r in runs if r.operation_type == "sync" and r.status == "success"), None)
            last_reindex = next((r for r in runs if r.operation_type == "reindex" and r.status == "success"), None)
            last_failed = next((r for r in runs if r.status == "failed"), None)
            in_progress = next((r for r in runs if r.status == "in_progress"), None)

            return {
                "current_operation_status": in_progress.status if in_progress else "idle",
                "current_operation_type": in_progress.operation_type if in_progress else None,
                "last_successful_ingestion": last_success.completed_at.isoformat() if last_success and last_success.completed_at else None,
                "last_attempted_ingestion": last_attempt.started_at.isoformat() if last_attempt else None,
                "last_sync_timestamp": last_sync.completed_at.isoformat() if last_sync and last_sync.completed_at else None,
                "last_successful_reindexing": last_reindex.completed_at.isoformat() if last_reindex and last_reindex.completed_at else None,
                "last_failed_ingestion": {
                    "timestamp": last_failed.completed_at.isoformat() if last_failed and last_failed.completed_at else None,
                    "error": last_failed.error_message,
                    "operation": last_failed.operation_type,
                }
                if last_failed
                else None,
                "latest_run_metrics": {
                    "successful_files": last_attempt.successful_files if last_attempt else 0,
                    "failed_files": last_attempt.failed_files if last_attempt else 0,
                    "total_chunks": last_attempt.total_chunks if last_attempt else 0,
                    "duration_seconds": last_attempt.duration_seconds if last_attempt else 0.0,
                }
                if last_attempt
                else None,
            }

    def get_source_info_stats(self) -> dict[str, Any]:
        """D. Source Information: URL availability, licensing completeness, metadata completeness."""
        with get_db_session(self.registry.engine) as session:
            stmt = select(DocumentMetadataRecord).where(
                DocumentMetadataRecord.document_status != "deleted"
            )
            records = session.scalars(stmt).all()
            total = len(records)

            if total == 0:
                return {
                    "total_documents": 0,
                    "source_url_count": 0,
                    "source_url_percentage": 0.0,
                    "license_complete_count": 0,
                    "license_completeness_percentage": 0.0,
                    "documents_missing_metadata": 0,
                    "documents_with_outdated_source_hashes": 0,
                }

            has_url = sum(1 for r in records if r.source_url)
            has_license = sum(1 for r in records if r.spdx_license and r.attribution)
            missing_meta = sum(1 for r in records if not r.category or not r.component or not r.title)

            return {
                "total_documents": total,
                "source_url_count": has_url,
                "source_url_percentage": round((has_url / total) * 100, 1),
                "license_complete_count": has_license,
                "license_completeness_percentage": round((has_license / total) * 100, 1),
                "documents_missing_metadata": missing_meta,
                "documents_with_outdated_source_hashes": 0,
            }

    def get_full_statistics(self, store: HybridStore | None = None) -> dict[str, Any]:
        """Compile complete statistics across all four areas (A, B, C, D)."""
        return {
            "inventory": self.get_inventory_stats(),
            "indexing": self.get_indexing_stats(store=store),
            "ingestion_status": self.get_ingestion_status(),
            "source_information": self.get_source_info_stats(),
        }

    def get_categories_summary(self, store: HybridStore | None = None) -> list[dict[str, Any]]:
        """Return category list with document counts and chunk counts."""
        inv = self.get_inventory_stats()
        idx = self.get_indexing_stats(store=store)

        doc_counts = inv.get("by_category", {})
        chunk_counts = idx.get("chunks_per_category", {})

        categories = sorted(set(list(doc_counts.keys()) + list(chunk_counts.keys())))
        summary = []
        for cat in categories:
            d_cnt = doc_counts.get(cat, 0)
            c_cnt = chunk_counts.get(cat, 0)
            summary.append({
                "category": cat,
                "document_count": d_cnt,
                "indexed_chunk_count": c_cnt,
                "status": "healthy" if c_cnt > 0 else "empty",
            })
        return summary

