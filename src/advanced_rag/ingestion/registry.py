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
        """Save or update an uploaded document record in file registry and database."""
        data = self._read_registry()
        doc_id = record["document_id"]
        record["origin"] = "uploaded"
        if "uploaded_at" not in record or not record["uploaded_at"]:
            record["uploaded_at"] = datetime.now(UTC).isoformat()
        data[doc_id] = record
        self._write_registry(data)

        # Mirror into persistent database registry
        try:
            from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry

            p_reg = PersistentDocumentRegistry()
            p_reg.register(record)
        except Exception as exc:
            logger.debug("Database registry mirror skipped: %s", exc)

        return record

    def get(self, document_id: str) -> dict[str, Any] | None:
        """Retrieve a document record by document_id (uploaded or built-in)."""
        data = self._read_registry()
        if document_id in data:
            return data[document_id]

        # Check built-in documents: match by exact document_id, or by stem for backward compatibility
        built_in_docs = self.get_built_in_documents()
        for doc in built_in_docs:
            if doc["document_id"] == document_id:
                return doc
            if f"builtin_{Path(doc['source']).stem.lower()}" == document_id:
                return doc
        return None

    def delete(self, document_id: str) -> bool:
        """Remove an uploaded document record. Built-in documents cannot be deleted."""
        # Built-in documents cannot be deleted
        if self.get(document_id) and self.get(document_id).get("origin") == "built-in":
            return False
        data = self._read_registry()
        if document_id in data:
            del data[document_id]
            self._write_registry(data)
            try:
                from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry

                p_reg = PersistentDocumentRegistry()
                p_reg.delete(document_id)
            except Exception as exc:
                logger.debug("Database registry delete mirror skipped: %s", exc)
            return True
        return False

    def get_built_in_documents(self) -> list[dict[str, Any]]:
        """Scan corpus directory recursively to return built-in document entries."""
        if not self.corpus_dir.exists():
            return []

        from advanced_rag.ingestion.extractors.factory import EXTRACTOR_REGISTRY

        built_in = []
        seen_canonical: set[Path] = set()

        # Deterministic recursive discovery: sort by relative POSIX path
        candidate_paths = sorted(
            [
                p
                for p in self.corpus_dir.rglob("*")
                if p.is_file()
                and not any(part.startswith(".") for part in p.relative_to(self.corpus_dir).parts)
                and not p.name.endswith("_manifest.json")
                and p.name != "manifest.json"
                and p.suffix.lower() in EXTRACTOR_REGISTRY
            ],
            key=lambda p: p.relative_to(self.corpus_dir).as_posix(),
        )

        for path in candidate_paths:
            try:
                canonical = path.resolve()
            except Exception:
                canonical = path
            if canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)

            rel_path = path.relative_to(self.corpus_dir).as_posix()
            stem = path.stem.lower()

            # Preserve backward-compatible doc_id for top-level files;
            # generate hierarchical deterministic doc_id for nested files.
            if path.parent == self.corpus_dir:
                doc_id = f"builtin_{stem}"
            else:
                clean_rel = (
                    rel_path.rsplit(".", 1)[0]
                    .replace("/", "_")
                    .replace("\\", "_")
                    .replace("-", "_")
                    .lower()
                )
                doc_id = f"builtin_{clean_rel}"

            # Derive human-readable title
            title = path.stem.replace("-", " ").replace("_", " ").title()
            if path.suffix.lower() in (".md", ".markdown"):
                try:
                    from advanced_rag.ingestion.extractors.markdown import parse_front_matter

                    meta, _ = parse_front_matter(path.read_text(encoding="utf-8"))
                    if meta.get("title"):
                        title = meta["title"]
                except Exception:
                    pass

            built_in.append({
                "document_id": doc_id,
                "filename": path.name,
                "file_type": path.suffix.lstrip(".").lower(),
                "title": title,
                "storage_path": str(canonical),
                "source": rel_path,
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
