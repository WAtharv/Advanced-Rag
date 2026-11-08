"""CSV document extractor with delimiter detection and row-aware column context preservation."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import normalize_text

logger = logging.getLogger(__name__)


def _detect_delimiter(sample: str) -> str:
    """Safely detect delimiter from sample text with comma fallback."""
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        # Fallback heuristic: check which delimiter occurs most frequently on the first line
        first_line = sample.splitlines()[0] if sample else ""
        counts = {d: first_line.count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


class CsvExtractor(BaseExtractor):
    """Extracts CSV tables into structured, row-aware text blocks retaining header context."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"CSV file is empty or does not exist: {source}")

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

        cleaned_text = normalize_text(text, preserve_code_blocks=False)
        if not cleaned_text.strip():
            raise ValueError(f"CSV file is empty or contains no text: {source}")

        delimiter = _detect_delimiter(cleaned_text[:4096])

        reader = csv.reader(io.StringIO(cleaned_text), delimiter=delimiter)
        try:
            rows = [r for r in reader if any(field.strip() for field in r)]
        except Exception as exc:
            raise ValueError(f"Failed parsing CSV in {source}: {exc}") from exc

        if not rows:
            raise ValueError(f"CSV file contains no data rows: {source}")

        headers = [h.strip() for h in rows[0]]
        data_rows = rows[1:]
        if not data_rows:
            raise ValueError(f"CSV file contains only headers with no data: {source}")

        sections: list[Section] = []
        body_parts: list[str] = []

        for idx, row in enumerate(data_rows, start=1):
            row_title = f"Row {idx}"
            fields: list[str] = []
            for h_idx, val in enumerate(row):
                header_name = (
                    headers[h_idx]
                    if h_idx < len(headers) and headers[h_idx]
                    else f"Column_{h_idx + 1}"
                )
                v_clean = val.strip()
                if v_clean:
                    fields.append(f"{header_name}: {v_clean}")

            row_body = "\n".join(fields) if fields else "(empty row)"
            sections.append(
                Section(
                    title=row_title,
                    body=row_body,
                    metadata={
                        "row": idx,
                        "row_number": idx,
                        "total_rows": len(data_rows),
                        "file_type": "csv",
                        "extraction_format": "csv",
                        "section": row_title,
                    },
                )
            )
            body_parts.append(f"## {row_title}\n\n{row_body}")

        return Document(
            source=source,
            title=title,
            body="\n\n".join(body_parts),
            doc_type="csv",
            metadata={
                "file_type": "csv",
                "extraction_format": "csv",
                "total_rows": len(data_rows),
                "headers": headers,
                "delimiter": delimiter,
            },
            sections=sections,
            file_type="csv",
        )
