"""`rag-ingest` - build the vector index and manage document lifecycle in Qdrant.

Multi-format CLI supporting:
- Single file and directory ingestion across Markdown, PDF, DOCX, TXT, CSV, HTML, and JSON
- Corpus synchronization with differential state detection (NEW, MODIFIED, DELETED, UNCHANGED)
- Safe document deletion with multi-chunk cleanup and path safety guards
- Re-indexing and collection inspection commands
- Dry-run reporting with format breakdown and character statistics
- Configurable batch processing with progress reporting
- Protected baseline collection guards (k8s_ops, k8s_ops_bge_small)
- Idempotent upserting and transactional replace-before-delete
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from advanced_rag.certs import enable_system_trust_store
from advanced_rag.config import get_settings
from advanced_rag.ingestion.loader import CORPUS_DIR, chunk_corpus, load_path
from advanced_rag.ingestion.sync import (
    PROTECTED_BASELINE_COLLECTIONS,
    CorpusSynchronizer,
)
from advanced_rag.observability import setup_logging
from advanced_rag.retrieval.embeddings import ModelUnavailableError
from advanced_rag.retrieval.vectorstore import HybridStore

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-ingest",
        description="Index multi-format documentation, synchronize corpus, and manage document lifecycles in Qdrant.",
    )
    # Target input path or action subcommand
    parser.add_argument(
        "target",
        nargs="?",
        type=str,
        default=None,
        help="input file or directory to ingest, or action (sync, delete, reindex, status, inspect)",
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        type=str,
        default=None,
        help="document identifier or source path for delete action",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="input file or directory path (alternative to positional argument)",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="corpus directory (backward-compatible alias for --path)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="single file path to ingest (alternative to --path)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="target Qdrant collection name (default: from settings without changing settings)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="number of chunks to embed and upsert per batch (default: 64)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="drop and rebuild the target collection first (forbidden on baseline collections)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="extract, chunk, and report without writing to Qdrant",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail immediately on any unreadable or malformed document instead of skipping",
    )
    parser.add_argument(
        "--seed-sql",
        action="store_true",
        help="also seed the Text2SQL operations database",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="safely synchronize corpus with Qdrant index (detects NEW, MODIFIED, DELETED)",
    )
    parser.add_argument(
        "--delete",
        type=str,
        default=None,
        help="delete a document and its chunks by ID or source path",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="re-index the target corpus into the specified collection",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="inspect collection health, document counts, and point statistics",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="prune stale documents during synchronization (requires --confirm)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm destructive operations (prune, delete, reindex)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    enable_system_trust_store()

    target_collection = args.collection or settings.qdrant_collection

    if args.recreate and target_collection in PROTECTED_BASELINE_COLLECTIONS:
        print(
            f"Error: Protected baseline collection '{target_collection}' cannot be dropped or recreated.",
            file=sys.stderr,
        )
        return 1

    # Resolve action mode: subcommands or flags
    action: str | None = None
    target_lower = str(args.target).lower() if args.target else None
    if target_lower in ("sync", "delete", "reindex", "status", "inspect"):
        action = target_lower
    elif args.delete:
        action = "delete"
    elif args.status:
        action = "status"
    elif args.reindex:
        action = "reindex"
    elif args.sync:
        action = "sync"

    # =========================================================================
    # ACTION: STATUS / INSPECT
    # =========================================================================
    if action in ("status", "inspect"):
        syncer = CorpusSynchronizer(settings=settings, collection=target_collection)
        info = syncer.inspect_collection()
        print(f"\n--- Collection Status: '{target_collection}' ---")
        print(f"Collection Exists:   {info['exists']}")
        print(f"Health Status:       {info['health']}")
        print(f"Total Points:        {info['total_points']}")
        print(f"Indexed Documents:   {info['documents_count']}")
        if info.get("formats"):
            fmt_str = ", ".join(f"{k}: {v}" for k, v in sorted(info["formats"].items()))
            print(f"Document Formats:    {fmt_str}")
        if info.get("categories"):
            cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(info["categories"].items()))
            print(f"Categories:          {cat_str}")

        # Persistent metadata registry statistics
        try:
            from advanced_rag.ingestion.stats import KnowledgeBaseStatsService

            stats_svc = KnowledgeBaseStatsService(settings=settings)
            inv = stats_svc.get_inventory_stats()
            src_info = stats_svc.get_source_info_stats()
            print(f"Metadata Registry:   {inv['total_documents']} tracked documents")
            print(f"Source URL Coverage: {src_info['source_url_percentage']}%")
            print(f"License Compliance:  {src_info['license_completeness_percentage']}%")
        except Exception as exc:
            logger.debug("Database stats summary skipped: %s", exc)

        print("\nBaseline Collections:")
        for b_name, b_info in info.get("baseline_collections", {}).items():
            print(f"  {b_name}: {'exists (' + str(b_info['points']) + ' points)' if b_info['exists'] else 'missing'}")
        print()
        return 0

    # =========================================================================
    # ACTION: DELETE
    # =========================================================================
    if action == "delete":
        doc_identifier = args.delete or args.identifier
        if not doc_identifier:
            print("Error: Document identifier or source path is required for deletion.", file=sys.stderr)
            return 1

        syncer = CorpusSynchronizer(settings=settings, collection=target_collection)
        try:
            res = syncer.delete_document(
                identifier=doc_identifier,
                confirm=args.confirm,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(
                    f"[Dry Run] Document '{doc_identifier}' in collection '{target_collection}': "
                    f"matched {res['matched_points']} point(s). No points deleted."
                )
            else:
                print(
                    f"Deleted document '{doc_identifier}' from '{target_collection}': "
                    f"removed {res['deleted_chunks']} chunk(s)."
                )
            return 0
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Deletion error: {exc}", file=sys.stderr)
            return 1

    # =========================================================================
    # ACTION: SYNC
    # =========================================================================
    if action == "sync":
        raw_path = (
            args.path
            or args.corpus
            or (Path(args.target) if args.target and args.target.lower() != "sync" else None)
            or CORPUS_DIR
        )
        sync_target = settings.absolute(raw_path) if not Path(raw_path).is_absolute() else Path(raw_path)

        if not sync_target.exists():
            print(f"Error: Target path does not exist: {sync_target}", file=sys.stderr)
            return 1

        syncer = CorpusSynchronizer(settings=settings, collection=target_collection)
        try:
            plan = syncer.create_plan(sync_target, ignore_errors=not args.strict)
            plan.print_summary()

            if args.dry_run:
                print("Dry run complete: no modifications made to Qdrant.")
                return 0

            if plan.deleted_documents and args.prune and not args.confirm:
                print(
                    f"Error: Pruning {len(plan.deleted_documents)} stale document(s) requires explicit --confirm flag.",
                    file=sys.stderr,
                )
                return 1

            res = syncer.execute_sync(
                plan=plan,
                prune=args.prune,
                confirm=args.confirm,
                batch_size=args.batch_size,
                dry_run=False,
            )
            print(
                f"Synchronization complete on '{target_collection}': "
                f"{res['upserted_chunks']} chunk(s) upserted, "
                f"{res['deleted_obsolete_chunks']} obsolete chunk(s) replaced, "
                f"{res['pruned_stale_documents']} stale document(s) pruned ({res['pruned_chunks']} points). "
                f"Final collection points: {res['final_point_count']}."
            )
            return 0
        except ModelUnavailableError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"Synchronization error: {exc}", file=sys.stderr)
            return 1

    # =========================================================================
    # ACTION: REINDEX
    # =========================================================================
    if action == "reindex":
        if not args.confirm:
            print(
                f"Error: Re-indexing collection '{target_collection}' requires explicit --confirm flag.",
                file=sys.stderr,
            )
            return 1

    # =========================================================================
    # STANDARD INGESTION / REINDEX
    # =========================================================================
    raw_target = (
        args.path
        or args.corpus
        or args.file
        or (Path(args.target) if args.target and args.target.lower() != "reindex" else None)
        or CORPUS_DIR
    )
    target_path = Path(raw_target)
    target_path = settings.absolute(target_path) if not target_path.is_absolute() else target_path

    if not target_path.exists():
        print(f"Error: Target path does not exist: {target_path}", file=sys.stderr)
        return 1

    print(f"Ingesting from: {target_path} ({'directory' if target_path.is_dir() else 'file'})")

    try:
        documents = load_path(target_path, ignore_errors=not args.strict)
    except Exception as exc:
        print(f"Error loading documents from {target_path}: {exc}", file=sys.stderr)
        return 1

    chunks = chunk_corpus(documents)
    print(f"Loaded {len(documents)} document(s) -> {len(chunks)} chunk(s)")

    # Format breakdown reporting
    format_counts = Counter(
        doc.file_type or Path(doc.source).suffix.lstrip(".").lower() or "text" for doc in documents
    )
    format_str = ", ".join(f"{fmt}: {count}" for fmt, count in sorted(format_counts.items()))
    print(f"Document formats: {format_str}")

    if chunks:
        sizes = sorted(len(c.text) for c in chunks)
        median = sizes[len(sizes) // 2]
        print(f"Chunk characters: min={sizes[0]} median={median} max={sizes[-1]}")

    if args.dry_run:
        print("\n--- Dry Run Report (no writes to Qdrant) ---")
        sample_count = min(3, len(chunks))
        for idx, chunk in enumerate(chunks[:sample_count], start=1):
            print(f"\n[Sample Chunk {idx}/{sample_count}]")
            print(f"Citation:    {chunk.citation()}")
            print(f"Source:      {chunk.source}")
            print(f"Section:     {chunk.section}")
            print(f"File Type:   {chunk.file_type}")
            print(f"Length:      {len(chunk.text)} characters")
            print("Preview:")
            preview = chunk.text[:240].replace("\n", " ")
            print(f"  {preview}...")
        print(f"\nDry run complete for {len(documents)} document(s) and {len(chunks)} chunk(s).")
        return 0

    if args.recreate and target_collection in PROTECTED_BASELINE_COLLECTIONS:
        print(
            f"Error: Protected baseline collection '{target_collection}' cannot be dropped or recreated.",
            file=sys.stderr,
        )
        return 1

    # Deduplicate chunks having identical chunk IDs to ensure clean deterministic 1:1 points
    seen_ids: set[str] = set()
    unique_chunks = []
    duplicate_count = 0
    for chunk in chunks:
        if chunk.id in seen_ids:
            duplicate_count += 1
            continue
        seen_ids.add(chunk.id)
        unique_chunks.append(chunk)

    if duplicate_count:
        print(
            f"Deduplicated {duplicate_count} chunk(s) with identical content IDs "
            f"({len(unique_chunks)} unique points remain)"
        )

    store = HybridStore(settings=settings, collection=target_collection)
    try:
        store.ensure_collection(recreate=args.recreate)
        total_chunks = len(unique_chunks)
        batch_size = max(1, args.batch_size)

        total_batches = (total_chunks + batch_size - 1) // batch_size
        print(
            f"Indexing {total_chunks} chunk(s) into '{store.collection}' "
            f"in {total_batches} batch(es) (batch size {batch_size})..."
        )
        written = store.upsert(unique_chunks, batch_size=batch_size)
        print(f"Successfully indexed {written} chunk(s) into '{store.collection}' (total {store.count()})")
    except ModelUnavailableError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Ingestion error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    if args.seed_sql:
        from advanced_rag.text2sql.database import seed_database

        rows = seed_database()
        print(f"Seeded operations database ({settings.sql_dialect}): {rows} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
