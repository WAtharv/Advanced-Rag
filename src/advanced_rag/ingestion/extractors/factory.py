"""Factory for instantiating format-specific extractors."""

from __future__ import annotations

from pathlib import Path

from advanced_rag.ingestion.extractors.base import BaseExtractor
from advanced_rag.ingestion.extractors.csv import CsvExtractor
from advanced_rag.ingestion.extractors.docx import DocxExtractor
from advanced_rag.ingestion.extractors.html import HtmlExtractor
from advanced_rag.ingestion.extractors.json import JsonExtractor
from advanced_rag.ingestion.extractors.markdown import MarkdownExtractor
from advanced_rag.ingestion.extractors.pdf import PdfExtractor
from advanced_rag.ingestion.extractors.text import TextExtractor

EXTRACTOR_REGISTRY: dict[str, type[BaseExtractor]] = {
    ".md": MarkdownExtractor,
    ".markdown": MarkdownExtractor,
    ".pdf": PdfExtractor,
    ".docx": DocxExtractor,
    ".txt": TextExtractor,
    ".csv": CsvExtractor,
    ".html": HtmlExtractor,
    ".htm": HtmlExtractor,
    ".json": JsonExtractor,
}


def get_extractor(extension_or_path: str | Path) -> BaseExtractor:
    """Return an extractor matching the file extension, case-insensitively.

    Args:
        extension_or_path: A file path (Path or str) or extension (e.g. '.pdf', 'PDF').

    Returns:
        An instance of BaseExtractor.

    Raises:
        ValueError: If the file extension is not supported.
    """
    if isinstance(extension_or_path, Path):
        ext = extension_or_path.suffix.lower()
    else:
        ext_str = str(extension_or_path).strip()
        # If it looks like a file name or path, extract the suffix
        if "." in ext_str:
            ext = ("." + ext_str.rsplit(".", 1)[-1]).lower()
        else:
            ext = f".{ext_str.lower()}"

    extractor_cls = EXTRACTOR_REGISTRY.get(ext)
    if not extractor_cls:
        supported = ", ".join(sorted(EXTRACTOR_REGISTRY.keys()))
        raise ValueError(
            f"Unsupported file extension {ext!r}. Supported extensions: {supported}"
        )
    return extractor_cls()

