"""Markdown document extractor with YAML front matter support."""

from __future__ import annotations

from pathlib import Path

from advanced_rag.ingestion.extractors.base import BaseExtractor, Document

#: Front matter keys promoted to first-class chunk fields; the rest become metadata.
_RESERVED = {"title", "doc_type"}


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` fenced block into a flat dict plus the remaining body."""
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        return {}, text

    meta: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, "\n".join(lines[closing + 1 :]).lstrip()


class MarkdownExtractor(BaseExtractor):
    """Extracts markdown runbooks, preserving front matter and headings."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(raw)
        source = path.relative_to(root).as_posix() if root else path.name
        return Document(
            source=source,
            title=meta.get("title") or path.stem.replace("-", " ").title(),
            body=body,
            doc_type=meta.get("doc_type", "runbook"),
            metadata={k: v for k, v in meta.items() if k not in _RESERVED},
            sections=None,  # Preserves markdown-aware chunker splitting on # headings
        )

