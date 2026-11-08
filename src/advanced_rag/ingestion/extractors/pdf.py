"""PDF document extractor using pypdf with page-by-page tracking and metadata extraction."""

from __future__ import annotations

import logging
from pathlib import Path

import pypdf
from pypdf.errors import PdfReadError

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import normalize_text

logger = logging.getLogger(__name__)


def _detect_page_heading(text: str) -> str | None:
    """Detect a prominent heading at the beginning of a page, if present."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None
    first = lines[0]
    # Check if first line contains a colon separator (e.g. "Cluster Architecture Guidelines: Details...")
    if ":" in first and len(first.split(":", 1)[0]) < 60:
        candidate = first.split(":", 1)[0].strip()
        if candidate.istitle() or candidate.isupper():
            return candidate
    # Check if first line looks like a title/heading (short, not ending in period/comma)
    if len(first) < 100 and not first.endswith((".", ",", ";", ":")):
        if first.startswith(("#", "1.", "2.", "3.", "4.", "5.", "Section", "Chapter")) or first.isupper() or first.istitle():
            return first.lstrip("#").strip()
    return None


class PdfExtractor(BaseExtractor):
    """Extracts text page-by-page from PDF files, preserving page numbers and document metadata."""

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

            total_pages = len(reader.pages)
            if total_pages == 0:
                raise ValueError(f"PDF contains no pages: {source}")

            doc_metadata = {
                "file_type": "pdf",
                "extraction_format": "pdf",
                "page_count": total_pages,
            }

            # Extract document metadata if available in PDF dictionary
            if reader.metadata:
                if reader.metadata.title:
                    meta_title = str(reader.metadata.title).strip()
                    if meta_title:
                        title = meta_title
                        doc_metadata["title"] = meta_title
                if reader.metadata.author:
                    doc_metadata["author"] = str(reader.metadata.author).strip()
                if reader.metadata.subject:
                    doc_metadata["subject"] = str(reader.metadata.subject).strip()
                if reader.metadata.creation_date:
                    doc_metadata["creation_date"] = str(reader.metadata.creation_date)

            sections: list[Section] = []
            full_text_parts: list[str] = []

            for idx, page in enumerate(reader.pages, start=1):
                try:
                    page_text = (page.extract_text() or "").strip()
                except Exception as exc:
                    logger.warning("Failed extracting page %d of %s: %s", idx, source, exc)
                    page_text = ""

                cleaned_page = normalize_text(page_text, preserve_code_blocks=True)
                if not cleaned_page:
                    continue  # Gracefully skip blank pages

                page_heading = _detect_page_heading(cleaned_page)
                section_title = f"Page {idx} - {page_heading}" if page_heading else f"Page {idx}"

                sections.append(
                    Section(
                        title=section_title,
                        body=cleaned_page,
                        metadata={
                            "page": idx,
                            "page_number": idx,
                            "page_count": total_pages,
                            "file_type": "pdf",
                            "extraction_format": "pdf",
                            "section": section_title,
                        },
                    )
                )
                full_text_parts.append(f"## {section_title}\n\n{cleaned_page}")

            if not sections:
                raise ValueError(
                    f"PDF contains {total_pages} page(s) but no extractable text. "
                    "Scanned or image-only PDFs require OCR processing."
                )

            body = "\n\n".join(full_text_parts)
            return Document(
                source=source,
                title=title,
                body=body,
                doc_type="pdf",
                metadata=doc_metadata,
                sections=sections,
                file_type="pdf",
            )
        except (PdfReadError, Exception) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"Corrupted or invalid PDF {source}: {exc}") from exc
