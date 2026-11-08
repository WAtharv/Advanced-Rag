"""Markdown document extractor with YAML front matter support and structure preservation."""

from __future__ import annotations

from pathlib import Path

from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import normalize_text

#: Front matter keys promoted to first-class chunk/document fields; the rest become metadata.
_RESERVED = {"title", "doc_type", "category", "component", "source_url", "source_version"}


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` fenced block into a flat dict plus the remaining body.

    Handles standard key: value pairs, quoted strings, and list values.
    """
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        return {}, text

    meta: dict[str, str] = {}
    current_key: str | None = None
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, _, value = line.partition(":")
            current_key = key.strip()
            meta[current_key] = value.strip().strip("\"'")
        elif current_key and (stripped.startswith("- ") or line.startswith((" ", "\t"))):
            # Append multi-line or list item to current key
            existing = meta.get(current_key, "")
            val_item = stripped.lstrip("- ").strip("\"'")
            meta[current_key] = f"{existing}, {val_item}".lstrip(", ")

    body = "\n".join(lines[closing + 1 :]).lstrip()
    return meta, body


class MarkdownExtractor(BaseExtractor):
    """Extracts markdown documents, preserving front matter, heading hierarchy, and code blocks."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Markdown file is empty or does not exist: {source}")

        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError(f"Markdown file is empty: {source}")

        meta, raw_body = parse_front_matter(raw)
        body = normalize_text(raw_body, preserve_code_blocks=True)
        if not body.strip():
            raise ValueError(f"Markdown file contains no readable content: {source}")

        title = meta.get("title") or path.stem.replace("-", " ").replace("_", " ").title()
        category = meta.get("category")
        if not category and "/" in source:
            category = source.split("/")[0]

        f_type = path.suffix.lstrip(".").lower() or "md"
        doc_meta = {
            "file_type": f_type,
            "extraction_format": "markdown",
            **{k: v for k, v in meta.items() if k not in _RESERVED},
        }
        for k in ("category", "component", "source_url", "source_version"):
            if k in meta:
                doc_meta[k] = meta[k]

        return Document(
            source=source,
            title=title,
            body=body,
            doc_type=meta.get("doc_type", "runbook"),
            metadata=doc_meta,
            sections=None,  # Preserves markdown-aware chunker splitting on # headings
            file_type=f_type,
            category=category,
            component=meta.get("component"),
            source_url=meta.get("source_url"),
            source_version=meta.get("source_version"),
        )
