"""PDF document extractor using pypdf."""

from __future__ import annotations

import logging
from pathlib import Path

import pypdf
from pypdf.errors import PdfReadError

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document

logger = logging.getLogger(__name__)


class PdfExtractor(BaseExtractor):
    """Extracts text page-by-page from PDF files."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"PDF file is empty or does not exist: {source}")

        try:
            reader = pypdf.PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    decrypted = reader.decrypt("")
                    if not decrypted:
                        raise ValueError(f"PDF is encrypted with a non-empty password: {source}")
                except Exception as exc:
                    raise ValueError(f"Cannot read encrypted PDF {source}: {exc}") from exc

            # Read document title if set in PDF metadata
            if reader.metadata and reader.metadata.title:
                meta_title = str(reader.metadata.title).strip()
                if meta_title:
                    title = meta_title

            sections: list[Section] = []
            full_text_parts: list[str] = []

            for idx, page in enumerate(reader.pages, start=1):
                try:
                    page_text = (page.extract_text() or "").strip()
                except Exception as exc:
                    logger.warning("Failed extracting page %d of %s: %s", idx, source, exc)
                    page_text = ""

                if not page_text:
                    continue  # Gracefully skip empty/blank pages

                section_title = f"Page {idx}"
                sections.append(
                    Section(
                        title=section_title,
                        body=page_text,
                        metadata={
                            "page": idx,
                            "page_number": idx,
                            "file_type": "pdf",
                        },
                    )
                )
                full_text_parts.append(f"## {section_title}\n\n{page_text}")

            if not sections:
                if len(reader.pages) > 0:
                    raise ValueError(
                        f"PDF contains {len(reader.pages)} page(s) but no extractable text. "
                        "Scanned or image-only PDFs require OCR processing."
                    )
                raise ValueError(f"PDF contains no pages: {source}")

            return Document(
                source=source,
                title=title,
                body="\n\n".join(full_text_parts),
                doc_type="pdf",
                metadata={"file_type": "pdf", "page_count": len(reader.pages)},
                sections=sections,
            )
        except (PdfReadError, Exception) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"Corrupted or invalid PDF {source}: {exc}") from exc

