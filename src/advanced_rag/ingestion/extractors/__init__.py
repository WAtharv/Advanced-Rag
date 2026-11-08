"""Multi-format document extractors."""

from __future__ import annotations

from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import is_stack_trace_or_log, normalize_text
from advanced_rag.ingestion.extractors.csv import CsvExtractor
from advanced_rag.ingestion.extractors.docx import DocxExtractor
from advanced_rag.ingestion.extractors.factory import EXTRACTOR_REGISTRY, get_extractor
from advanced_rag.ingestion.extractors.html import HtmlExtractor
from advanced_rag.ingestion.extractors.json import JsonExtractor
from advanced_rag.ingestion.extractors.markdown import MarkdownExtractor, parse_front_matter
from advanced_rag.ingestion.extractors.pdf import PdfExtractor
from advanced_rag.ingestion.extractors.text import TextExtractor

__all__ = [
    "BaseExtractor",
    "CsvExtractor",
    "DocxExtractor",
    "Document",
    "EXTRACTOR_REGISTRY",
    "HtmlExtractor",
    "JsonExtractor",
    "MarkdownExtractor",
    "PdfExtractor",
    "TextExtractor",
    "get_extractor",
    "is_stack_trace_or_log",
    "normalize_text",
    "parse_front_matter",
]

