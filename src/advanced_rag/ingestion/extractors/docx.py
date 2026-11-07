"""DOCX document extractor using python-docx."""

from __future__ import annotations

import logging
from pathlib import Path

import docx

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document

logger = logging.getLogger(__name__)


class DocxExtractor(BaseExtractor):
    """Extracts headings, paragraphs, and tables from DOCX documents."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"DOCX file is empty or does not exist: {source}")

        try:
            doc = docx.Document(str(path))
        except Exception as exc:
            raise ValueError(f"Corrupted or invalid DOCX file {source}: {exc}") from exc

        try:
            if doc.core_properties and doc.core_properties.title:
                prop_title = doc.core_properties.title.strip()
                if prop_title:
                    title = prop_title
        except Exception:
            pass

        sections: list[Section] = []
        current_title = "Overview"
        current_paras: list[str] = []

        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            if p.style and p.style.name and p.style.name.lower().startswith("heading"):
                if current_paras:
                    sections.append(
                        Section(
                            title=current_title,
                            body="\n\n".join(current_paras),
                            metadata={"file_type": "docx"},
                        )
                    )
                    current_paras = []
                current_title = text
            else:
                current_paras.append(text)

        # Extract table contents as markdown-like structured text
        table_rows_text: list[str] = []
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                if any(cells):
                    table_rows_text.append(" | ".join(cells))
        if table_rows_text:
            current_paras.append("\n\n" + "\n".join(table_rows_text))

        if current_paras:
            sections.append(
                Section(
                    title=current_title,
                    body="\n\n".join(current_paras),
                    metadata={"file_type": "docx"},
                )
            )

        if not sections:
            raise ValueError(f"DOCX document contains no readable text: {source}")

        body = "\n\n".join(f"## {s.title}\n\n{s.body}" if s.title else s.body for s in sections)
        return Document(
            source=source,
            title=title,
            body=body,
            doc_type="docx",
            metadata={"file_type": "docx"},
            sections=sections,
        )

