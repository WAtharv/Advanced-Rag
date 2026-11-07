"""JSON document extractor using standard library json module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document


def _format_value(val: Any, indent: int = 0) -> str:
    """Recursively formats JSON objects and arrays into clean human-readable text."""
    prefix = "  " * indent
    if isinstance(val, dict):
        lines: list[str] = []
        for k, v in val.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}{k}:\n{_format_value(v, indent + 1)}")
            else:
                lines.append(f"{prefix}{k}: {v}")
        return "\n".join(lines)
    elif isinstance(val, list):
        lines = []
        for idx, item in enumerate(val, start=1):
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}- [{idx}]:\n{_format_value(item, indent + 1)}")
            else:
                lines.append(f"{prefix}- {item}")
        return "\n".join(lines)
    return f"{prefix}{val}"


class JsonExtractor(BaseExtractor):
    """Extracts JSON files, converting nested structures into readable sections."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"JSON file is empty or does not exist: {source}")

        raw_text = path.read_text(encoding="utf-8")
        if not raw_text.strip():
            raise ValueError(f"JSON file is empty: {source}")

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in {source}: {exc}") from exc

        sections: list[Section] = []
        body_parts: list[str] = []

        if isinstance(data, list):
            for idx, item in enumerate(data, start=1):
                sec_title = f"Item {idx}"
                sec_body = _format_value(item)
                sections.append(
                    Section(
                        title=sec_title,
                        body=sec_body,
                        metadata={"index": idx, "file_type": "json"},
                    )
                )
                body_parts.append(f"## {sec_title}\n\n{sec_body}")
        elif isinstance(data, dict):
            if "title" in data and isinstance(data["title"], str):
                title = data["title"].strip()
            for key, val in data.items():
                sec_title = str(key)
                sec_body = _format_value(val)
                sections.append(
                    Section(
                        title=sec_title,
                        body=sec_body,
                        metadata={"key": key, "file_type": "json"},
                    )
                )
                body_parts.append(f"## {sec_title}\n\n{sec_body}")
        else:
            sec_body = str(data)
            sections.append(
                Section(title="Data", body=sec_body, metadata={"file_type": "json"})
            )
            body_parts.append(sec_body)

        if not sections or not any(s.body.strip() for s in sections):
            raise ValueError(f"JSON file contains no extractable data: {source}")

        return Document(
            source=source,
            title=title,
            body="\n\n".join(body_parts),
            doc_type="json",
            metadata={"file_type": "json"},
            sections=sections,
        )

