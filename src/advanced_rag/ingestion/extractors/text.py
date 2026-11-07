"""Plain text document extractor using Python standard library."""

from __future__ import annotations

import re
from pathlib import Path

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


class TextExtractor(BaseExtractor):
    """Extracts plain text with encoding auto-detection and paragraph splitting."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Text file is empty or does not exist: {source}")

        raw_bytes = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")

        paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
        if not paragraphs:
            raise ValueError(f"Text file contains no readable text: {source}")

        body = "\n\n".join(paragraphs)
        sections = [Section(title="", body=body, metadata={"file_type": "txt"})]
        return Document(
            source=source,
            title=title,
            body=body,
            doc_type="text",
            metadata={"file_type": "txt"},
            sections=sections,
        )

