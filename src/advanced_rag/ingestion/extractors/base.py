"""Base document extractor protocol and common data models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from advanced_rag.ingestion.chunker import Section


@dataclass
class Document:
    source: str
    title: str
    body: str
    doc_type: str = "runbook"
    metadata: dict[str, Any] = field(default_factory=dict)
    sections: list[Section] | None = None
    file_type: str | None = None
    category: str | None = None
    component: str | None = None
    source_url: str | None = None
    source_version: str | None = None


class BaseExtractor(ABC):
    """Abstract interface for file-format specific extractors."""

    @abstractmethod
    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        """Extract a structured Document from a file on disk."""
        raise NotImplementedError

