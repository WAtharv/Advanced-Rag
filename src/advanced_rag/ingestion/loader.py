"""Load the document corpus from disk across multiple supported formats.

Supported formats: Markdown (.md), PDF (.pdf), Word (.docx), Plain Text (.txt),
CSV (.csv), HTML (.html, .htm), and JSON (.json).
"""

from __future__ import annotations

import logging
from pathlib import Path

from advanced_rag.config import get_settings
from advanced_rag.ingestion.chunker import chunk_document
from advanced_rag.ingestion.extractors.base import Document
from advanced_rag.ingestion.extractors.factory import EXTRACTOR_REGISTRY, get_extractor
from advanced_rag.ingestion.extractors.markdown import parse_front_matter
from advanced_rag.models import Chunk

logger = logging.getLogger(__name__)

CORPUS_DIR = Path("data/corpus")

# Re-export for backward compatibility with external imports / existing tests
__all__ = [
    "CORPUS_DIR",
    "Document",
    "chunk_corpus",
    "load_corpus",
    "load_document",
    "parse_front_matter",
]


def load_document(path: Path, *, root: Path | None = None) -> Document:
    """Extract and load a single document using the appropriate format extractor."""
    extractor = get_extractor(path)
    return extractor.extract(path, root=root)


def load_corpus(
    directory: Path | None = None,
    *,
    ignore_errors: bool = True,
) -> list[Document]:
    """Discover and load all supported documents under the corpus directory.

    Args:
        directory: Root corpus directory. Defaults to data/corpus.
        ignore_errors: If True, logs a warning for malformed files and continues
            ingesting valid documents. If False, raises on the first error.

    Returns:
        List of extracted Document objects.

    Raises:
        FileNotFoundError: If corpus directory does not exist or has no supported files.
    """
    settings = get_settings()
    root = settings.absolute(directory or CORPUS_DIR)
    if not root.exists():
        raise FileNotFoundError(f"corpus directory not found: {root}")

    paths = sorted(
        [
            p
            for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in EXTRACTOR_REGISTRY
        ],
        key=lambda p: p.relative_to(root).as_posix(),
    )
    if not paths:
        supported = ", ".join(sorted(EXTRACTOR_REGISTRY.keys()))
        raise FileNotFoundError(f"no supported documents ({supported}) under {root}")

    documents: list[Document] = []
    for path in paths:
        try:
            documents.append(load_document(path, root=root))
        except Exception as exc:
            if not ignore_errors:
                raise
            logger.warning("Skipping unreadable or malformed document %s: %s", path, exc)

    logger.info("Loaded %d documents from %s", len(documents), root)
    return documents


def chunk_corpus(
    documents: list[Document],
    *,
    min_chunk_size: int | None = None,
) -> list[Chunk]:
    """Turn loaded documents into retrievable chunks preserving provenance and metadata."""
    settings = get_settings()
    chunks: list[Chunk] = []
    for document in documents:
        produced = chunk_document(
            text=document.body,
            source=document.source,
            title=document.title,
            doc_type=document.doc_type,
            metadata=document.metadata,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            min_chunk_size=min_chunk_size,
            sections=document.sections,
        )
        logger.info("%s -> %d chunks", document.source, len(produced))
        chunks.extend(produced)
    return chunks
