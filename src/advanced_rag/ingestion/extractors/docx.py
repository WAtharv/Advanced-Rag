"""DOCX document extractor using python-docx with table and heading preservation."""

from __future__ import annotations

import logging
from pathlib import Path

import docx
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import normalize_text

logger = logging.getLogger(__name__)


def _format_docx_table(table) -> str:
    """Format a docx Table object as a clean GitHub-Flavored Markdown table with headers."""
    if not table.rows:
        return ""

    rows_data: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ").replace("|", "\\|") for cell in row.cells]
        if any(cells):
            rows_data.append(cells)

    if not rows_data:
        return ""

    header_row = rows_data[0]
    num_cols = len(header_row)
    separator = ["---"] * num_cols

    lines: list[str] = [
        "| " + " | ".join(header_row) + " |",
        "| " + " | ".join(separator) + " |",
    ]

    for data_row in rows_data[1:]:
        # Pad row to match number of columns
        padded = data_row + [""] * (num_cols - len(data_row))
        lines.append("| " + " | ".join(padded[:num_cols]) + " |")

    return "\n".join(lines)


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

        doc_metadata: dict[str, str] = {
            "file_type": "docx",
            "extraction_format": "docx",
        }

        # Extract core properties if available
        try:
            props = doc.core_properties
            if props:
                if props.title and props.title.strip():
                    title = props.title.strip()
                    doc_metadata["title"] = title
                if props.author and props.author.strip():
                    doc_metadata["author"] = props.author.strip()
                if props.category and props.category.strip():
                    doc_metadata["category"] = props.category.strip()
                if props.created:
                    doc_metadata["created"] = str(props.created)
                if props.revision:
                    doc_metadata["revision"] = str(props.revision)
        except Exception:
            pass

        sections: list[Section] = []
        current_title = "Overview"
        current_blocks: list[str] = []

        # Extract paragraphs, headings, and tables in original document order
        for child in doc.element.body:
            if isinstance(child, CT_P):
                p = Paragraph(child, doc)
                text = normalize_text(p.text, preserve_code_blocks=True)
                if not text:
                    continue

                style_name = (p.style.name if p.style and p.style.name else "").lower()
                if style_name.startswith("heading") or style_name.startswith("title"):
                    if current_blocks:
                        sec_body = "\n\n".join(current_blocks)
                        sections.append(
                            Section(
                                title=current_title,
                                body=sec_body,
                                metadata={"file_type": "docx", "section": current_title},
                            )
                        )
                        current_blocks = []
                    current_title = text
                else:
                    current_blocks.append(text)
            elif isinstance(child, CT_Tbl):
                table = Table(child, doc)
                table_md = _format_docx_table(table)
                if table_md:
                    current_blocks.append(table_md)

        if current_blocks:
            sec_body = "\n\n".join(current_blocks)
            sections.append(
                Section(
                    title=current_title,
                    body=sec_body,
                    metadata={"file_type": "docx", "section": current_title},
                )
            )

        if not sections or not any(s.body.strip() for s in sections):
            raise ValueError(f"DOCX document contains no readable text: {source}")

        body = "\n\n".join(f"## {s.title}\n\n{s.body}" if s.title else s.body for s in sections)
        return Document(
            source=source,
            title=title,
            body=body,
            doc_type="docx",
            metadata=doc_metadata,
            sections=sections,
            file_type="docx",
            category=doc_metadata.get("category"),
        )
