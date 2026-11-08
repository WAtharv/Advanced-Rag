"""Text cleaning and normalization utilities for multi-format document extraction.

Ensures consistent normalization across Markdown, PDF, DOCX, TXT, CSV, HTML, and JSON
while strictly preserving code blocks, YAML indentation, tables, timestamps,
technical identifiers, and stack traces.
"""

from __future__ import annotations

import re

# Characters to normalize
_UNICODE_SPACES = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000\ufeff]")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d]")
_CURLY_QUOTES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "--",
}

# Consecutive newlines
_EXCESSIVE_NEWLINES = re.compile(r"\n{3,}")


def normalize_text(text: str, *, preserve_code_blocks: bool = True) -> str:
    """Normalize text while preserving technical content, code fences, and indentation.

    Args:
        text: Raw extracted document text.
        preserve_code_blocks: If True, protects fenced code blocks from aggressive whitespace alteration.

    Returns:
        Cleaned, normalized text string.
    """
    if not text:
        return ""

    # 1. Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Normalize unicode spacing artifacts
    text = _UNICODE_SPACES.sub(" ", text)
    text = _ZERO_WIDTH.sub("", text)

    # 3. Normalize typographic quotes and dashes
    for src, dst in _CURLY_QUOTES.items():
        text = text.replace(src, dst)

    # 4. If code fences exist and should be preserved, process block by block
    if preserve_code_blocks and ("```" in text or "~~~" in text):
        lines = text.splitlines()
        cleaned_lines: list[str] = []
        in_fence = False
        fence_marker = ""
        for line in lines:
            stripped = line.strip()
            if not in_fence:
                if stripped.startswith("```") or stripped.startswith("~~~"):
                    in_fence = True
                    fence_marker = stripped[:3]
                    cleaned_lines.append(line.rstrip())
                else:
                    # Strip trailing whitespace but keep leading indentation (e.g. lists, YAML)
                    cleaned_lines.append(line.rstrip())
            else:
                if stripped.startswith(fence_marker):
                    in_fence = False
                    fence_marker = ""
                # Inside code block: preserve exact line as is
                cleaned_lines.append(line)
        text = "\n".join(cleaned_lines)
    else:
        # Strip trailing whitespace on each line, keep leading indentation
        text = "\n".join(line.rstrip() for line in text.splitlines())

    # 5. Collapse 3+ consecutive newlines to 2 newlines (standard markdown paragraph spacing)
    text = _EXCESSIVE_NEWLINES.sub("\n\n", text)
    return text.strip()


_LOG_OR_TRACE_PATTERNS = re.compile(
    r"(\[\s*(INFO|WARN|WARNING|ERROR|FATAL|DEBUG|CRITICAL)\s*\]|"
    r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}|"
    r"Traceback \(most recent call last\):|"
    r"^\s*File\s+\".*\",\s+line\s+\d+|"
    r"goroutine \d+|"
    r"panic:\s+|"
    r"Exception in thread |"
    r"Stack trace:|at org\.|at com\.|Caused by:)",
    re.IGNORECASE | re.MULTILINE,
)


def is_stack_trace_or_log(text: str) -> bool:
    """Detect whether a passage represents a stack trace or structured operational log."""
    return bool(_LOG_OR_TRACE_PATTERNS.search(text))
