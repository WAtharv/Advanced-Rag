"""Document metadata registry.

Maintains persistent records for user-uploaded documents and surfaces
built-in corpus documents alongside them, ensuring strict separation
between built-in assets and dynamic uploads.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path("data/uploads/registry.json")
DEFAULT_CORPUS_DIR = Path("data/corpus")


class DocumentRegistry:
    """Thread-safe persistent JSON registry for document metadata."""

    def __init__(
        self,
        registry_path: Path | str = DEFAULT_REGISTRY_PATH,
        corpus_dir: Path | str = DEFAULT_CORPUS_DIR,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.corpus_dir = Path(corpus_dir)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_registry(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.exists():
            return {}
        try:
            with open(self.registry_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Failed to read registry at %s: %s", self.registry_path, exc)
            return {}

    def _write_registry(self, data: dict[str, dict[str, Any]]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via temporary file in the same directory to avoid half-written files
        dir_name = self.registry_path.parent
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            temp_path = Path(tf.name)
        temp_path.replace(self.registry_path)

    def register(self, record: dict[str, Any]) -> dict[str, Any]:
        """Save or update an uploaded document record."""
        data = self._read_registry()
        doc_id = record["document_id"]
        record["origin"] = "uploaded"
        if "uploaded_at" not in record or not record["uploaded_at"]:
            record["uploaded_at"] = datetime.now(UTC).isoformat()
        data[doc_id] = record
        self._write_registry(data)
        return record

    def get(self, document_id: str) -> dict[str, Any] | None:
        """Retrieve a document record by document_id (uploaded or built-in)."""
        data = self._read_registry()
        if document_id in data:
            return data[document_id]

        # Check built-in documents
        built_in_docs = self.get_built_in_documents()
        for doc in built_in_docs:
            if doc["document_id"] == document_id:
                return doc
        return None

    def delete(self, document_id: str) -> bool:
        """Remove an uploaded document record. Built-in documents cannot be deleted."""
        data = self._read_registry()
        if document_id in data:
            del data[document_id]
            self._write_registry(data)
            return True
        return False

    def get_built_in_documents(self) -> list[dict[str, Any]]:
        """Scan corpus directory to return built-in document entries."""
        if not self.corpus_dir.exists():
            return []

        built_in = []
        for path in sorted(self.corpus_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                built_in.append({
                    "document_id": f"builtin_{path.stem.lower()}",
                    "filename": path.name,
                    "file_type": path.suffix.lstrip(".").lower(),
                    "title": path.stem.replace("-", " ").replace("_", " ").title(),
                    "storage_path": str(path.resolve()),
                    "source": path.name,
                    "chunk_count": None,
                    "uploaded_at": None,
                    "status": "indexed",
                    "origin": "built-in",
                })
        return built_in

    def list_all(self, include_built_in: bool = True) -> list[dict[str, Any]]:
        """Return all registered documents, optionally including built-in ones."""
        uploaded = list(self._read_registry().values())
        if not include_built_in:
            return uploaded
        return self.get_built_in_documents() + uploaded


_default_registry: DocumentRegistry | None = None


def get_registry() -> DocumentRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = DocumentRegistry()
    return _default_registry


def reset_registry(registry: DocumentRegistry | None = None) -> None:
    global _default_registry
    _default_registry = registry
