"""`rag-ingest` - build the vector index and seed the operations database."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from advanced_rag.certs import enable_system_trust_store
from advanced_rag.config import get_settings
from advanced_rag.ingestion.loader import chunk_corpus, load_corpus
from advanced_rag.observability import setup_logging
from advanced_rag.retrieval.embeddings import ModelUnavailableError
from advanced_rag.retrieval.vectorstore import get_store

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-ingest", description="Index the Kubernetes ops corpus into Qdrant."
    )
    parser.add_argument(
        "--corpus", type=Path, default=None, help="corpus directory (default data/corpus)"
    )
    parser.add_argument(
        "--recreate", action="store_true", help="drop and rebuild the collection first"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="chunk and report without writing to Qdrant"
    )
    parser.add_argument(
        "--seed-sql", action="store_true", help="also seed the Text2SQL operations database"
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="safely replace modified documents and delete obsolete chunks",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    # The first ingestion downloads the ONNX models; do it through the OS trust
    # store so a corporate TLS proxy does not fail the download.
    enable_system_trust_store()

    documents = load_corpus(args.corpus)
    chunks = chunk_corpus(documents)
    print(f"{len(documents)} documents -> {len(chunks)} chunks")

    if chunks:
        sizes = sorted(len(c.text) for c in chunks)
        median = sizes[len(sizes) // 2]
        print(f"chunk chars: min={sizes[0]} median={median} max={sizes[-1]}")

    if args.dry_run:
        for chunk in chunks[:3]:
            print(f"\n--- {chunk.citation()} ({len(chunk.text)} chars)")
            print(chunk.text[:280])
        return 0

    store = get_store()
    try:
        store.ensure_collection(recreate=args.recreate)
        if args.sync and not args.recreate:
            from collections import defaultdict

            by_source: dict[str, list] = defaultdict(list)
            for chunk in chunks:
                by_source[chunk.source].append(chunk)

            total_written = 0
            total_deleted = 0
            for src, doc_chunks in by_source.items():
                res = store.replace_document(doc_chunks, source=src)
                total_written += res["upserted"]
                total_deleted += res["deleted_obsolete"]
            print(
                f"synced {len(by_source)} documents: {total_written} chunks written, "
                f"{total_deleted} obsolete chunks removed (total {store.count()})"
            )
        else:
            written = store.upsert(chunks)
        print(f"indexed {written} chunks into '{store.collection}' (total {store.count()})")
    except ModelUnavailableError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    finally:
        # Embedded Qdrant holds a file lock; release it rather than leaving the
        # interpreter to tear the client down at shutdown.
        store.close()

    if args.seed_sql:
        from advanced_rag.text2sql.database import seed_database

        rows = seed_database()
        print(f"seeded operations database ({settings.sql_dialect}): {rows} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
