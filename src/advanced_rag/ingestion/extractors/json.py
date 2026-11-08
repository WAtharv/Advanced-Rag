"""JSON document extractor with structured splitting and key preservation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document


def _format_json_value(val: Any, indent: int = 0) -> str:
    """Recursively formats JSON objects and arrays into structured, human-readable text."""
    prefix = "  " * indent
    if isinstance(val, dict):
        lines: list[str] = []
        for k, v in val.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}{k}:\n{_format_json_value(v, indent + 1)}")
            else:
                lines.append(f"{prefix}{k}: {v}")
        return "\n".join(lines)
    elif isinstance(val, list):
        lines = []
        for idx, item in enumerate(val, start=1):
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}- [{idx}]:\n{_format_json_value(item, indent + 1)}")
            else:
                lines.append(f"{prefix}- {item}")
        return "\n".join(lines)
    return f"{prefix}{val}"


def _get_item_label(item: Any, default_idx: int) -> str:
    """Extract a meaningful identifier for an item if present (e.g. name, id, title)."""
    if isinstance(item, dict):
        for key in ("name", "title", "id", "alert", "service", "resource"):
            if key in item and item[key]:
                return f"Item {default_idx} - {item[key]}"
    return f"Item {default_idx}"


class JsonExtractor(BaseExtractor):
    """Extracts JSON files, converting nested structures and arrays into structured, retrievable sections."""

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
                sec_title = _get_item_label(item, idx)
                sec_body = _format_json_value(item)
                sections.append(
                    Section(
                        title=sec_title,
                        body=sec_body,
                        metadata={
                            "index": idx,
                            "file_type": "json",
                            "extraction_format": "json",
                            "section": sec_title,
                        },
                    )
                )
                body_parts.append(f"## {sec_title}\n\n{sec_body}")
        elif isinstance(data, dict):
            if "title" in data and isinstance(data["title"], str) and data["title"].strip():
                title = data["title"].strip()

            for key, val in data.items():
                if key == "title" and isinstance(val, str):
                    continue

                if isinstance(val, list) and all(isinstance(elem, dict) for elem in val):
                    # Structured sub-list (e.g. alerts, rules, pods)
                    for item_idx, elem in enumerate(val, start=1):
                        item_label = _get_item_label(elem, item_idx)
                        sec_title = f"{key} - {item_label}"
                        sec_body = _format_json_value(elem)
                        sections.append(
                            Section(
                                title=sec_title,
                                body=sec_body,
                                metadata={
                                    "key": key,
                                    "item_index": item_idx,
                                    "file_type": "json",
                                    "extraction_format": "json",
                                    "section": sec_title,
                                },
                            )
                        )
                        body_parts.append(f"## {sec_title}\n\n{sec_body}")
                else:
                    sec_title = str(key)
                    sec_body = _format_json_value(val)
                    sections.append(
                        Section(
                            title=sec_title,
                            body=sec_body,
                            metadata={
                                "key": key,
                                "file_type": "json",
                                "extraction_format": "json",
                                "section": sec_title,
                            },
                        )
                    )
                    body_parts.append(f"## {sec_title}\n\n{sec_body}")
        else:
            sec_body = str(data)
            sections.append(
                Section(
                    title="Data",
                    body=sec_body,
                    metadata={"file_type": "json", "extraction_format": "json"},
                )
            )
            body_parts.append(sec_body)

        if not sections or not any(s.body.strip() for s in sections):
            raise ValueError(f"JSON file contains no extractable data: {source}")

        return Document(
            source=source,
            title=title,
            body="\n\n".join(body_parts),
            doc_type="json",
            metadata={"file_type": "json", "extraction_format": "json"},
            sections=sections,
            file_type="json",
        )
