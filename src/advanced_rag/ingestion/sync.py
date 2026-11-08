"""Corpus synchronization, document lifecycle management, and Qdrant cleanup.

Implements Task 41 (safe document deletion) and Task 44 (corpus synchronization).
Provides state detection (NEW, MODIFIED, DELETED, UNCHANGED), staged synchronization,
atomic/staged upsert-before-delete replacement, and safety guards for protected collections.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from advanced_rag.config import Settings, get_settings
from advanced_rag.ingestion.extractors.base import Document
from advanced_rag.ingestion.loader import CORPUS_DIR, chunk_corpus, load_path
from advanced_rag.retrieval.vectorstore import HybridStore

logger = logging.getLogger(__name__)

# Baseline collections that must never be wiped, pruned, or deleted
PROTECTED_BASELINE_COLLECTIONS = frozenset({"k8s_ops", "k8s_ops_bge_small"})


class SyncStatus(str, Enum):
    """Document state relative to the vector index."""

    NEW = "new"  # Present on disk, absent in Qdrant
    MODIFIED = "modified"  # Present in both, but content hash or structure differs
    DELETED = "deleted"  # Present in Qdrant, absent on disk (stale)
    UNCHANGED = "unchanged"  # Present in both with matching content hash


@dataclass
class DocumentSyncItem:
    """Detailed synchronization state for an individual document."""

    source: str
    document_id: str
    status: SyncStatus
    title: str = ""
    file_type: str = ""
    disk_content_hash: str | None = None
    indexed_content_hash: str | None = None
    disk_chunk_count: int = 0
    indexed_chunk_count: int = 0
    point_ids: list[str] = field(default_factory=list)
    reason: str = ""
    doc_object: Document | None = None


@dataclass
class SyncPlan:
    """Structured plan produced prior to applying synchronization changes."""

    collection: str
    corpus_path: Path
    items: list[DocumentSyncItem] = field(default_factory=list)

    @property
    def new_documents(self) -> list[DocumentSyncItem]:
        return [item for item in self.items if item.status == SyncStatus.NEW]

    @property
    def modified_documents(self) -> list[DocumentSyncItem]:
        return [item for item in self.items if item.status == SyncStatus.MODIFIED]

    @property
    def deleted_documents(self) -> list[DocumentSyncItem]:
        return [item for item in self.items if item.status == SyncStatus.DELETED]

    @property
    def unchanged_documents(self) -> list[DocumentSyncItem]:
        return [item for item in self.items if item.status == SyncStatus.UNCHANGED]

    def summary_dict(self) -> dict[str, int]:
        return {
            "new": len(self.new_documents),
            "modified": len(self.modified_documents),
            "deleted": len(self.deleted_documents),
            "unchanged": len(self.unchanged_documents),
            "total": len(self.items),
        }

    def print_summary(self) -> None:
        summary = self.summary_dict()
        print(f"\n--- Synchronization Plan for '{self.collection}' ---")
        print(f"Target Path: {self.corpus_path}")
        print(f"  [+] NEW:       {summary['new']:>4} document(s) to index")
        print(f"  [~] MODIFIED:  {summary['modified']:>4} document(s) to update")
        print(f"  [-] DELETED:   {summary['deleted']:>4} stale document(s) in Qdrant")
        print(f"  [=] UNCHANGED: {summary['unchanged']:>4} document(s) in sync")
        print(f"  TOTAL:         {summary['total']:>4} document(s) evaluated\n")


def _get_candidate_hashes(doc: Document, file_path: Path | None = None) -> set[str]:
    """Compute candidate SHA-256 hashes for content comparison across ingestion stages."""
    hashes: set[str] = set()
    if doc.body:
        hashes.add(hashlib.sha256(doc.body.encode()).hexdigest())
        hashes.add(hashlib.sha256(doc.body.strip().encode()).hexdigest())

    if file_path and file_path.is_file():
        try:
            txt = file_path.read_text(encoding="utf-8", errors="replace")
            # Raw text hash
            hashes.add(hashlib.sha256(txt.encode()).hexdigest())
            hashes.add(hashlib.sha256(txt.strip().encode()).hexdigest())

            # Frontmatter-stripped body hash
            parts = txt.split("---", 2)
            if len(parts) >= 3:
                raw_body = parts[2]
                hashes.add(hashlib.sha256(raw_body.encode()).hexdigest())
                hashes.add(hashlib.sha256(raw_body.strip().encode()).hexdigest())
                hashes.add(hashlib.sha256(raw_body.lstrip().encode()).hexdigest())
        except Exception:
            pass
    return hashes


def _is_in_scope(source: str, target_path: Path, root_dir: Path) -> bool:
    """Check if an indexed document source falls within the scope of target_path."""
    try:
        abs_target = target_path.resolve()
        abs_root = root_dir.resolve()
    except Exception:
        abs_target = target_path
        abs_root = root_dir

    if abs_target == abs_root or target_path == root_dir:
        return True

    # If target is a single file
    if target_path.is_file():
        try:
            rel = target_path.relative_to(root_dir).as_posix()
            return source == rel or source == target_path.as_posix()
        except ValueError:
            return source == target_path.name

    # If target is a subdirectory
    try:
        rel_prefix = target_path.relative_to(root_dir).as_posix().rstrip("/") + "/"
        norm_source = Path(source).as_posix()
        return norm_source.startswith(rel_prefix) or norm_source == rel_prefix.rstrip("/")
    except ValueError:
        return True


class CorpusSynchronizer:
    """Orchestrates corpus synchronization and lifecycle operations against Qdrant."""

    def __init__(
        self,
        settings: Settings | None = None,
        collection: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.collection = collection or self.settings.qdrant_collection

    def _get_store(self, store: HybridStore | None = None) -> tuple[HybridStore, bool]:
        """Obtain a HybridStore instance; returns (store, should_close)."""
        if store is not None:
            return store, False
        return HybridStore(settings=self.settings, collection=self.collection), True

    def create_plan(
        self,
        corpus_path: Path | str = CORPUS_DIR,
        store: HybridStore | None = None,
        ignore_errors: bool = True,
    ) -> SyncPlan:
        """Analyze disk documents and Qdrant points to produce an execution plan.

        Stages 1-4:
        1. Discover disk documents and retrieve indexed Qdrant points.
        2. Validate readable documents.
        3. Compare content hashes, chunk counts, and source identifiers.
        4. Assemble the detailed SyncPlan.
        """
        target = Path(corpus_path)
        if not target.is_absolute():
            target = self.settings.absolute(target)

        if not target.exists():
            raise FileNotFoundError(f"Target path does not exist: {target}")

        root_corpus = self.settings.absolute(CORPUS_DIR)

        # Stage 1 & 2: Load disk documents
        disk_docs = load_path(target, ignore_errors=ignore_errors)

        # Stage 1: Query indexed state from Qdrant
        st, should_close = self._get_store(store)
        try:
            indexed_docs = st.get_indexed_documents()
        finally:
            if should_close:
                st.close()

        # Build normalized lookups for indexed points
        indexed_by_source: dict[str, dict[str, Any]] = {}
        indexed_by_doc_id: dict[str, dict[str, Any]] = {}
        for src, info in indexed_docs.items():
            norm_src = Path(src).as_posix()
            indexed_by_source[norm_src] = info
            doc_id = info.get("document_id")
            if doc_id:
                indexed_by_doc_id[doc_id] = info

        matched_indexed_sources: set[str] = set()
        items: list[DocumentSyncItem] = []

        # Stage 3: Compare disk docs to indexed points
        for doc in disk_docs:
            norm_source = Path(doc.source).as_posix()
            doc_id = doc.metadata.get("document_id") or hashlib.sha256(norm_source.encode()).hexdigest()[:16]
            disk_hash = hashlib.sha256(doc.body.encode()).hexdigest()

            # Determine actual file path on disk for candidate hashing
            file_path = target if target.is_file() else (root_corpus / doc.source)
            if not file_path.exists():
                file_path = target / doc.source

            # Document-level candidate hashes (supporting Stage 2.7 legacy format)
            doc_body_hashes = {
                disk_hash,
                hashlib.sha256(doc.body.strip().encode()).hexdigest(),
            }
            if file_path and file_path.is_file():
                try:
                    txt = file_path.read_text(encoding="utf-8", errors="replace")
                    parts = txt.split("---", 2)
                    raw_body = parts[2] if len(parts) >= 3 else txt
                    doc_body_hashes.add(hashlib.sha256(raw_body.strip().encode()).hexdigest())
                    doc_body_hashes.add(hashlib.sha256(raw_body.lstrip().encode()).hexdigest())
                    doc_body_hashes.add(hashlib.sha256(txt.encode()).hexdigest())
                except Exception:
                    pass

            # Chunk-level candidate hashes (supporting Stage 3A/3B chunking format)
            try:
                disk_chunks = chunk_corpus([doc])
            except Exception:
                disk_chunks = []
            disk_chunk_hashes = {c.content_hash for c in disk_chunks if getattr(c, "content_hash", None)}

            # Match against indexed Qdrant points
            matched_info = indexed_by_source.get(norm_source) or indexed_by_doc_id.get(doc_id)

            if matched_info:
                matched_src = Path(matched_info["source"]).as_posix()
                matched_indexed_sources.add(matched_src)
                indexed_hashes = matched_info.get("content_hashes", set())
                indexed_chunks = matched_info.get("chunk_count", 0)
                point_ids = matched_info.get("point_ids", [])
                indexed_hash_sample = sorted(list(indexed_hashes))[0] if indexed_hashes else None

                # Document is UNCHANGED if:
                # 1. Chunk hashes match indexed hashes exactly (Stage 3A/3B engine), OR
                # 2. Document body hash matches indexed hash (Stage 2.7 engine)
                is_chunk_match = (
                    bool(disk_chunk_hashes)
                    and (disk_chunk_hashes == indexed_hashes)
                    and (len(disk_chunks) == indexed_chunks)
                )
                is_doc_body_match = bool(doc_body_hashes.intersection(indexed_hashes))

                if is_chunk_match or is_doc_body_match:
                    item = DocumentSyncItem(
                        source=norm_source,
                        document_id=doc_id,
                        status=SyncStatus.UNCHANGED,
                        title=doc.title,
                        file_type=doc.file_type or Path(norm_source).suffix.lstrip(".").lower() or "text",
                        disk_content_hash=disk_hash,
                        indexed_content_hash=indexed_hash_sample,
                        disk_chunk_count=indexed_chunks,
                        indexed_chunk_count=indexed_chunks,
                        point_ids=point_ids,
                        reason="Content hash matches indexed Qdrant points",
                        doc_object=doc,
                    )
                else:
                    item = DocumentSyncItem(
                        source=norm_source,
                        document_id=doc_id,
                        status=SyncStatus.MODIFIED,
                        title=doc.title,
                        file_type=doc.file_type or Path(norm_source).suffix.lstrip(".").lower() or "text",
                        disk_content_hash=disk_hash,
                        indexed_content_hash=indexed_hash_sample,
                        disk_chunk_count=len(disk_chunks),
                        indexed_chunk_count=indexed_chunks,
                        point_ids=point_ids,
                        reason=f"Content hash differs (disk: {disk_hash[:12]}, indexed: {str(indexed_hash_sample)[:12]})",
                        doc_object=doc,
                    )
            else:
                item = DocumentSyncItem(
                    source=norm_source,
                    document_id=doc_id,
                    status=SyncStatus.NEW,
                    title=doc.title,
                    file_type=doc.file_type or Path(norm_source).suffix.lstrip(".").lower() or "text",
                    disk_content_hash=disk_hash,
                    indexed_content_hash=None,
                    disk_chunk_count=0,
                    indexed_chunk_count=0,
                    point_ids=[],
                    reason="Document exists on disk but is not indexed in Qdrant",
                    doc_object=doc,
                )
            items.append(item)

        # Detect stale/deleted documents in Qdrant (scoped to target_path)
        for norm_src, info in indexed_by_source.items():
            if norm_src not in matched_indexed_sources:
                if _is_in_scope(norm_src, target, root_corpus):
                    item = DocumentSyncItem(
                        source=info["source"],
                        document_id=info.get("document_id", ""),
                        status=SyncStatus.DELETED,
                        title=info.get("title", ""),
                        file_type=info.get("file_type", ""),
                        disk_content_hash=None,
                        indexed_content_hash=sorted(list(info.get("content_hashes", set())))[0]
                        if info.get("content_hashes")
                        else None,
                        disk_chunk_count=0,
                        indexed_chunk_count=info.get("chunk_count", 0),
                        point_ids=info.get("point_ids", []),
                        reason="Indexed in Qdrant but source document no longer exists on disk",
                        doc_object=None,
                    )
                    items.append(item)

        return SyncPlan(collection=self.collection, corpus_path=target, items=items)

    def execute_sync(
        self,
        plan: SyncPlan,
        store: HybridStore | None = None,
        prune: bool = False,
        confirm: bool = False,
        batch_size: int = 64,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute a synchronization plan following stages 5-8.

        5. Ingest NEW and MODIFIED documents first.
        6. Verify successful ingestion before deleting obsolete points.
        7. Prune stale documents ONLY when explicitly enabled (--prune + --confirm).
        8. Verify final point counts and return results.
        """
        if self.collection in PROTECTED_BASELINE_COLLECTIONS:
            raise ValueError(
                f"Synchronization forbidden on protected baseline collection '{self.collection}'."
            )

        if dry_run:
            plan.print_summary()
            return {
                "collection": self.collection,
                "dry_run": True,
                "summary": plan.summary_dict(),
                "upserted_chunks": 0,
                "deleted_obsolete_chunks": 0,
                "pruned_stale_documents": 0,
                "pruned_chunks": 0,
            }

        st, should_close = self._get_store(store)
        p_reg = None
        run_id = None
        docs_to_ingest = []
        try:
            try:
                from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry

                p_reg = PersistentDocumentRegistry(settings=self.settings)
                run_id = p_reg.record_ingestion_run(
                    operation_type="sync",
                    collection_name=self.collection,
                    status="in_progress",
                )
            except Exception as exc:
                logger.debug("Persistent registry audit run tracking unavailable: %s", exc)

            st.ensure_collection()
            initial_count = st.count()
            logger.info("Starting sync on '%s' (initial points: %d)", self.collection, initial_count)

            upserted_chunks = 0
            deleted_obsolete_chunks = 0
            pruned_docs = 0
            pruned_chunks = 0

            # Stage 5 & 6: Ingest new and modified documents first
            docs_to_ingest = [
                item.doc_object
                for item in (plan.new_documents + plan.modified_documents)
                if item.doc_object is not None
            ]

            if docs_to_ingest:
                if p_reg:
                    for item in (plan.new_documents + plan.modified_documents):
                        p_reg.update_status(item.document_id, status="indexing")

                chunks = chunk_corpus(docs_to_ingest)
                logger.info("Chunked %d document(s) -> %d chunk(s)", len(docs_to_ingest), len(chunks))

                # Group chunks by source for atomic per-document replacement
                from collections import defaultdict

                by_source: dict[str, list] = defaultdict(list)
                for c in chunks:
                    by_source[c.source].append(c)

                for src, doc_chunks in by_source.items():
                    res = st.replace_document(doc_chunks, source=src)
                    upserted_chunks += res["upserted"]
                    deleted_obsolete_chunks += res["deleted_obsolete"]

                if p_reg:
                    for doc in docs_to_ingest:
                        norm_src = Path(doc.source).as_posix()
                        doc_id = (
                            doc.metadata.get("document_id")
                            or hashlib.sha256(norm_src.encode()).hexdigest()[:16]
                        )
                        doc_chunk_cnt = len(by_source.get(doc.source, []))
                        p_reg.register({
                            "document_id": doc_id,
                            "source": norm_src,
                            "title": doc.title,
                            "file_type": doc.file_type or Path(norm_src).suffix.lstrip(".").lower() or "text",
                            "content_hash": hashlib.sha256(doc.body.encode()).hexdigest(),
                            "category": doc.category,
                            "component": doc.component,
                            "doc_type": doc.doc_type,
                            "source_url": doc.source_url,
                            "source_repository": doc.metadata.get("source_repository"),
                            "source_version": doc.source_version,
                            "source_commit": doc.metadata.get("source_commit"),
                            "spdx_license": doc.metadata.get("spdx_license"),
                            "attribution": doc.metadata.get("attribution"),
                            "status": "indexed",
                            "chunk_count": doc_chunk_cnt,
                            "collection_name": self.collection,
                        })

            # Stage 7: Prune stale documents only when explicitly enabled
            if plan.deleted_documents:
                if prune:
                    if not confirm:
                        raise ValueError(
                            "Pruning stale documents requires explicit confirmation (--confirm)."
                        )
                    for item in plan.deleted_documents:
                        del_res = st.delete_document(item.source)
                        pruned_docs += 1
                        pruned_chunks += del_res.get("deleted_chunks", 0)
                        if p_reg:
                            p_reg.delete(item.document_id)
                    logger.info("Pruned %d stale document(s) (%d points)", pruned_docs, pruned_chunks)
                else:
                    logger.info(
                        "Found %d stale document(s) in Qdrant. Pruning skipped (requires --prune --confirm).",
                        len(plan.deleted_documents),
                    )

            # Stage 8: Verify final Qdrant point count
            final_count = st.count()
            logger.info(
                "Sync complete on '%s': final points=%d (delta=%+d)",
                self.collection,
                final_count,
                final_count - initial_count,
            )

            if p_reg and run_id:
                p_reg.complete_ingestion_run(
                    run_id=run_id,
                    status="success",
                    successful_files=len(docs_to_ingest),
                    failed_files=0,
                    total_chunks=upserted_chunks,
                    details=plan.summary_dict(),
                )

            return {
                "collection": self.collection,
                "dry_run": False,
                "summary": plan.summary_dict(),
                "initial_point_count": initial_count,
                "final_point_count": final_count,
                "upserted_chunks": upserted_chunks,
                "deleted_obsolete_chunks": deleted_obsolete_chunks,
                "pruned_stale_documents": pruned_docs,
                "pruned_chunks": pruned_chunks,
            }
        except Exception as exc:
            if p_reg and run_id:
                p_reg.complete_ingestion_run(
                    run_id=run_id,
                    status="failed",
                    successful_files=0,
                    failed_files=len(docs_to_ingest),
                    total_chunks=0,
                    error=str(exc),
                )
            raise
        finally:
            if should_close:
                st.close()

    def delete_document(
        self,
        identifier: str,
        store: HybridStore | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Safely delete a document and all its chunks from Qdrant."""
        if not identifier or not identifier.strip():
            raise ValueError("Document identifier cannot be empty.")

        clean_id = identifier.strip()
        if clean_id in ("*", "/", "\\", ".", "~") or clean_id.startswith("*"):
            raise ValueError(
                f"Unsafe or ambiguous document identifier '{identifier}'. Wildcards and root paths are forbidden."
            )

        if ".." in clean_id:
            raise ValueError(f"Path traversal detected in identifier '{identifier}'.")

        if self.collection in PROTECTED_BASELINE_COLLECTIONS:
            raise ValueError(
                f"Deletion forbidden on protected baseline collection '{self.collection}'."
            )

        st, should_close = self._get_store(store)
        try:
            point_ids = st.get_point_ids(clean_id)
            matched_count = len(point_ids)

            if dry_run:
                return {
                    "source": clean_id,
                    "collection": self.collection,
                    "matched_points": matched_count,
                    "deleted_chunks": 0,
                    "found": bool(matched_count),
                    "dry_run": True,
                }

            if not confirm:
                raise ValueError(
                    f"Deletion of document '{clean_id}' ({matched_count} points in '{self.collection}') "
                    f"requires explicit confirmation (pass confirm=True or --confirm)."
                )

            res = st.delete_document(clean_id, dry_run=False)
            res["collection"] = self.collection

            # Mirror deletion in persistent database registry
            try:
                from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry

                p_reg = PersistentDocumentRegistry(settings=self.settings)
                p_reg.delete(clean_id)
            except Exception as exc:
                logger.debug("Database registry delete mirror skipped: %s", exc)

            return res
        finally:
            if should_close:
                st.close()

    def inspect_collection(self, store: HybridStore | None = None) -> dict[str, Any]:
        """Inspect and report the health, point counts, and document breakdown of the collection."""
        st, should_close = self._get_store(store)
        try:
            exists = st.client.collection_exists(self.collection)
            if not exists:
                return {
                    "collection": self.collection,
                    "exists": False,
                    "total_points": 0,
                    "documents_count": 0,
                    "health": "COLLECTION_NOT_FOUND",
                }

            total_points = st.count()
            indexed_docs = st.get_indexed_documents()

            format_counts: Counter[str] = Counter()
            category_counts: Counter[str] = Counter()

            for info in indexed_docs.values():
                fmt = info.get("file_type") or "unknown"
                format_counts[fmt] += 1
                src = info.get("source", "")
                cat = src.split("/")[0] if "/" in src else "root"
                category_counts[cat] += 1

            # Check protected baseline collection states
            baseline_status = {}
            for base_col in PROTECTED_BASELINE_COLLECTIONS:
                if st.client.collection_exists(base_col):
                    cnt = st.client.count(base_col, exact=True).count
                    baseline_status[base_col] = {"exists": True, "points": cnt}
                else:
                    baseline_status[base_col] = {"exists": False, "points": 0}

            return {
                "collection": self.collection,
                "exists": True,
                "total_points": total_points,
                "documents_count": len(indexed_docs),
                "formats": dict(format_counts),
                "categories": dict(category_counts),
                "baseline_collections": baseline_status,
                "health": "HEALTHY" if total_points > 0 else "EMPTY",
            }
        finally:
            if should_close:
                st.close()
