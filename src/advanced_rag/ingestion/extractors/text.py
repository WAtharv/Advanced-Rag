"""Plain text document extractor with encoding fallbacks and log/stack trace preservation."""

from __future__ import annotations

import re
from pathlib import Path

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import is_stack_trace_or_log, normalize_text

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _split_text_into_logical_sections(text: str) -> list[str]:
    """Split text into logical sections while keeping stack traces and multi-line log entries intact."""
    raw_blocks = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    if not raw_blocks:
        return []

    merged: list[str] = []
    in_traceback = False
    traceback_accum: list[str] = []

    for block in raw_blocks:
        if is_stack_trace_or_log(block):
            in_traceback = True
            traceback_accum.append(block)
        elif in_traceback:
            # If line is indented or looks like part of the trace/log error
            if block.startswith(("File ", "at ", "Caused by:", "  ", "\t")):
                traceback_accum.append(block)
            else:
                merged.append("\n\n".join(traceback_accum))
                traceback_accum = []
                in_traceback = False
                merged.append(block)
        else:
            merged.append(block)

    if traceback_accum:
        merged.append("\n\n".join(traceback_accum))

    return merged or raw_blocks


class TextExtractor(BaseExtractor):
    """Extracts plain text with encoding auto-detection, normalizing line breaks while preserving logs."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Text file is empty or does not exist: {source}")

        raw_bytes = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")

        cleaned = normalize_text(text, preserve_code_blocks=True)
        if not cleaned:
            raise ValueError(f"Text file contains no readable text: {source}")

        sections_text = _split_text_into_logical_sections(cleaned)
        sections: list[Section] = []

        if len(sections_text) > 1 and sum(len(s) for s in sections_text) > 1200:
            for idx, sec_text in enumerate(sections_text, start=1):
                sec_title = f"Section {idx}"
                sections.append(
                    Section(
                        title=sec_title,
                        body=sec_text,
                        metadata={"file_type": "txt", "extraction_format": "text"},
                    )
                )
            body = "\n\n".join(f"## {s.title}\n\n{s.body}" for s in sections)
        else:
            body = cleaned
            sections = [
                Section(
                    title="Overview",
                    body=body,
                    metadata={"file_type": "txt", "extraction_format": "text"},
                )
            ]

        return Document(
            source=source,
            title=title,
            body=body,
            doc_type="text",
            metadata={"file_type": "txt", "extraction_format": "text"},
            sections=sections,
            file_type="txt",
        )
