"""Markdown-aware chunking.

Splitting on headings first keeps a runbook step with its own heading, which is
what makes the citation ("Pod Troubleshooting - CrashLoopBackOff") useful to the
engineer reading the answer. Only oversized sections fall back to a sliding
window over sentence boundaries.
"""

from __future__ import annotations

import hashlib
import re  # regular expression matching operations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from advanced_rag.models import Chunk

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Section:
    title: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


def split_sections(markdown: str) -> list[Section]:
    """Break a document at its headings, keeping each heading with its body.

    Ignores '#' lines inside fenced code blocks (``` or ~~~) so that code comments
    do not erroneously split code blocks into artificial sections.
    """
    lines = markdown.splitlines(keepends=True)
    in_fence = False
    fence_char = None
    fence_len = 0

    heading_indices: list[tuple[int, re.Match]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = True
                fence_char = stripped[0]
                m = re.match(r"^[`~]+", stripped)
                fence_len = len(m.group(0)) if m else 3
            else:
                m = _HEADING.match(line)
                if m:
                    heading_indices.append((idx, m))
        else:
            if (stripped.startswith("```") or stripped.startswith("~~~")) and stripped[0] == fence_char:
                m = re.match(r"^[`~]+", stripped)
                curr_len = len(m.group(0)) if m else 3
                if curr_len >= fence_len:
                    in_fence = False
                    fence_char = None
                    fence_len = 0

    if not heading_indices:
        return [Section(title="", body=markdown.strip())]

    sections: list[Section] = []
    first_idx = heading_indices[0][0]
    preamble = "".join(lines[:first_idx]).strip()
    if preamble:
        sections.append(Section(title="", body=preamble))

    for i, (line_idx, match) in enumerate(heading_indices):
        next_idx = heading_indices[i + 1][0] if i + 1 < len(heading_indices) else len(lines)
        body = "".join(lines[line_idx + 1 : next_idx]).strip()
        if body:
            sections.append(Section(title=match.group(2).strip(), body=body))
    return sections


def _extract_blocks(text: str) -> list[str]:
    """Break markdown text into blocks: fenced code blocks and paragraphs."""
    lines = text.splitlines(keepends=True)
    blocks: list[str] = []
    current_lines: list[str] = []
    in_fence = False
    fence_char = None
    fence_len = 0

    for line in lines:
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                if current_lines:
                    block_text = "".join(current_lines).strip()
                    if block_text:
                        blocks.append(block_text)
                    current_lines = []
                in_fence = True
                fence_char = stripped[0]
                m = re.match(r"^[`~]+", stripped)
                fence_len = len(m.group(0)) if m else 3
                current_lines.append(line)
            elif not stripped:
                if current_lines:
                    block_text = "".join(current_lines).strip()
                    if block_text:
                        blocks.append(block_text)
                    current_lines = []
            else:
                current_lines.append(line)
        else:
            current_lines.append(line)
            if (stripped.startswith("```") or stripped.startswith("~~~")) and stripped[0] == fence_char:
                m = re.match(r"^[`~]+", stripped)
                curr_len = len(m.group(0)) if m else 3
                if curr_len >= fence_len:
                    block_text = "".join(current_lines).strip()
                    if block_text:
                        blocks.append(block_text)
                    current_lines = []
                    in_fence = False
                    fence_char = None
                    fence_len = 0

    if current_lines:
        block_text = "".join(current_lines).strip()
        if block_text:
            blocks.append(block_text)

    return blocks


def _is_table_block(block: str) -> bool:
    """Check if a block represents a markdown table with header and separator."""
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    return len(lines) >= 2 and lines[0].startswith("|") and lines[0].endswith("|") and "---" in lines[1]


def _split_oversized_table(table_text: str, size: int) -> list[str]:
    """Split an oversized table while attaching column headers to every chunk."""
    lines = [l.strip() for l in table_text.splitlines() if l.strip()]
    if len(lines) <= 2:
        return [table_text]
    header = lines[0] + "\n" + lines[1]
    header_len = len(header) + 2
    data_rows = lines[2:]

    parts: list[str] = []
    curr_rows: list[str] = []
    curr_len = header_len

    for row in data_rows:
        row_cost = len(row) + 1
        if curr_rows and (curr_len + row_cost > size):
            parts.append(header + "\n" + "\n".join(curr_rows))
            curr_rows = [row]
            curr_len = header_len + row_cost
        else:
            curr_rows.append(row)
            curr_len += row_cost

    if curr_rows:
        parts.append(header + "\n" + "\n".join(curr_rows))

    return parts or [table_text]


def _split_oversized_code_block(code_block: str, size: int) -> list[str]:
    """Split an oversized fenced code block predictably while preserving valid fences."""
    lines = code_block.splitlines()
    if len(lines) <= 2:
        return [code_block]
    open_fence = lines[0]
    close_fence = lines[-1] if lines[-1].strip().startswith(("```", "~~~")) else "```"
    content_lines = lines[1:-1] if lines[-1].strip().startswith(("```", "~~~")) else lines[1:]

    parts: list[str] = []
    curr: list[str] = []
    curr_len = len(open_fence) + len(close_fence) + 2

    for line in content_lines:
        line_cost = len(line) + 1
        if curr and (curr_len + line_cost > size):
            parts.append(open_fence + "\n" + "\n".join(curr) + "\n" + close_fence)
            curr = [line]
            curr_len = len(open_fence) + len(close_fence) + 2 + line_cost
        else:
            curr.append(line)
            curr_len += line_cost

    if curr:
        parts.append(open_fence + "\n" + "\n".join(curr) + "\n" + close_fence)

    return parts or [code_block]


def _split_oversized_prose(text: str, size: int) -> list[str]:
    """Split oversized prose by sentence ends, then by words if a sentence exceeds size."""
    sentences = [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]
    if not sentences:
        return [text]
    pieces: list[str] = []
    for s in sentences:
        if len(s) <= size:
            pieces.append(s)
        else:
            words = s.split()
            w_curr: list[str] = []
            w_len = 0
            for w in words:
                if w_curr and (w_len + len(w) + 1 > size):
                    pieces.append(" ".join(w_curr))
                    w_curr = [w]
                    w_len = len(w)
                else:
                    w_curr.append(w)
                    w_len += len(w) + 1
            if w_curr:
                pieces.append(" ".join(w_curr))
    return pieces


def window(text: str, size: int, overlap: int) -> list[str]:
    """Pack text into <=`size` character windows that overlap by `overlap`.

    Preserves code fences, YAML key-values, indentation, and line breaks.
    """
    if len(text) <= size:
        return [text.strip()] if text.strip() else []

    blocks = _extract_blocks(text)
    pieces: list[str] = []
    for b in blocks:
        if len(b) <= size:
            pieces.append(b)
        elif b.startswith(("```", "~~~")):
            pieces.extend(_split_oversized_code_block(b, size))
        elif _is_table_block(b):
            pieces.extend(_split_oversized_table(b, size))
        else:
            pieces.extend(_split_oversized_prose(b, size))

    if not pieces:
        return []

    windows: list[str] = []
    current: list[str] = []
    length = 0

    for piece in pieces:
        piece_len = len(piece)
        sep_len = 2 if current else 0
        if current and (length + sep_len + piece_len > size):
            windows.append("\n\n".join(current))
            # Re-seed the next window with tail of this one for overlap
            tail: list[str] = []
            tail_len = 0
            for prev in reversed(current):
                p_cost = len(prev) + (2 if tail else 0)
                if tail_len + p_cost > overlap:
                    break
                tail.insert(0, prev)
                tail_len += p_cost
            current = tail
            length = tail_len
            sep_len = 2 if current else 0

        current.append(piece)
        length += sep_len + piece_len

    if current:
        windows.append("\n\n".join(current))

    return windows


def chunk_document(
    *,
    text: str | None = None,
    source: str,
    title: str,
    doc_type: str = "runbook",
    metadata: dict | None = None,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
    min_chunk_size: int | None = None,
    sections: list[Section] | None = None,
) -> list[Chunk]:
    """
    Get section title and body.
    Add title at the beginning of every chunk:
        Plain Text
            Pod Troubleshooting
            Check logs...
    If section is too large, use window() to split it.
    Create a Chunk object with:
        unique id
        chunk text
        source file
        title
        section name
        metadata
    Add chunk to the final list.
    """
    
    """Turn one markdown document into retrievable chunks."""
    chunks: list[Chunk] = []
    target_sections = sections if sections is not None else split_sections(text or "")
    for section in target_sections:
        # Prefixing the section title gives the embedding local context that the
        # body alone often lacks (bare command blocks, bullet fragments).
        prefix = section.title + "\n" if section.title else ""
        chunk_meta = dict(metadata or {})
        if hasattr(section, "metadata") and section.metadata:
            chunk_meta.update(section.metadata)
        doc_id = hashlib.sha256(source.encode()).hexdigest()[:16]
        f_type = chunk_meta.get("file_type") or Path(source).suffix.lstrip(".").lower() or "text"
        page = chunk_meta.get("page_number", chunk_meta.get("page"))
        row = chunk_meta.get("row_number", chunk_meta.get("row"))

        # Ensure standard metadata attributes are preserved in payload
        category = chunk_meta.get("category") or (source.split("/")[0] if "/" in source else None)
        if category and "category" not in chunk_meta:
            chunk_meta["category"] = category
        if "extraction_format" not in chunk_meta:
            chunk_meta["extraction_format"] = f_type
        ext_fmt = str(chunk_meta.get("extraction_format") or f_type)
        if "title" not in chunk_meta and title:
            chunk_meta["title"] = title
        if "document_id" not in chunk_meta:
            chunk_meta["document_id"] = doc_id

        if min_chunk_size is not None:
            min_len = min_chunk_size
        elif f_type in ("md", "markdown"):
            min_len = 40
        else:
            min_len = 1
        for part in window(section.body, chunk_size, chunk_overlap) or [section.body]:
            part_clean = part.strip()
            if not part_clean:
                continue
            body = (prefix + part_clean).strip()
            if len(body) < min_len:
                continue
            chunks.append(
                Chunk(
                    id=_chunk_id(source, section.title, body),
                    text=body,
                    source=source,
                    title=title,
                    section=section.title,
                    doc_type=doc_type,
                    file_type=f_type,
                    extraction_format=ext_fmt,
                    page_number=page,
                    row_number=row,
                    document_id=doc_id,
                    content_hash=hashlib.sha256(body.encode()).hexdigest(),
                    metadata=chunk_meta,
                )
            )
    return chunks


def _chunk_id(source: str, section: str, body: str) -> str:
    """
    Creates a unique ID using SHA-256 hash.
    
    This ensures:
        Same content → same ID
        Modified content → new ID
    """
    
    """Content-addressed id, so re-ingesting unchanged docs overwrites in place."""
    digest = hashlib.sha256((source + "|" + section + "|" + body).encode()).hexdigest()
    return source + "#" + digest[:16]



"""
Markdown Document
        ↓
split_sections()
        ↓
Section 1, Section 2, ...
        ↓
window() (if section is large)
        ↓
Chunk Objects
        ↓
Unique Hash ID
        ↓
Stored in Vector DB


    - So when retrieved, the LLM gets context + citation source, making answers like:
      From "Pod Troubleshooting", check container logs using kubectl logs.
"""
