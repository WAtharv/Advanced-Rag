"""Robust JSON extraction and validation utilities for LLM responses.

LLMs sometimes wrap JSON in markdown fences (```json ... ```), include conversational
preambles or trailing commentary, or introduce trailing commas. This module
cleans and normalizes JSON payloads and validates them against Pydantic models.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def clean_json_text(text: str) -> str:
    """Extract and clean raw JSON text from an LLM response."""
    text = text.strip()

    # 1. Strip markdown fences if present
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    fence_match = re.search(fence_pattern, text)
    if fence_match:
        text = fence_match.group(1).strip()

    # 2. Extract substring between outermost braces if there is commentary
    if not text.startswith("{") and "{" in text:
        start = text.find("{")
        end = text.rfind("}")
        if end > start:
            text = text[start : end + 1]

    # 3. Handle trailing commas before closing braces/brackets
    text = re.sub(r",\s*([\]}])", r"\1", text)

    return text


def parse_and_validate_json(raw_text: str, schema_model: type[T]) -> T:
    """Parse raw text into a validated Pydantic model instance.

    Raises ValidationError or json.JSONDecodeError if parsing fails completely.
    """
    cleaned = clean_json_text(raw_text)

    # First attempt: direct Pydantic json validation
    try:
        return schema_model.model_validate_json(cleaned)
    except (ValidationError, ValueError):
        pass

    # Second attempt: standard json.loads then model_validate
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return schema_model.model_validate(data)
    except Exception:
        pass

    # Third attempt: look for inner JSON object if string had nested quotes
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidate = cleaned[start : end + 1]
        data = json.loads(candidate)
        return schema_model.model_validate(data)

    # If all cleanup attempts failed, re-run model_validate_json to raise informative ValidationError
    return schema_model.model_validate_json(cleaned)

