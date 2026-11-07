"""Google Gemini backend, presenting the same interface as LLMClient.

Only ``__init__``, ``complete`` and ``count_tokens`` are provider-specific;
``complete_json`` and the ``LLMResult`` contract are inherited from LLMClient, so
every graph node and guardrail call site works unchanged when
``LLM_PROVIDER=gemini``. Claude-only knobs (effort, adaptive thinking, prompt
cache, server-side refusal fallbacks) are accepted for interface parity and
ignored — Gemini 2.5 manages its own thinking.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from advanced_rag.config import Settings, get_settings
from advanced_rag.llm.client import LLMClient, LLMResult

logger = logging.getLogger(__name__)

#: Gemini finish reasons that mean the model declined. Mapped to the app's
#: "refusal" stop_reason so complete_json raises LLMRefusedError like Claude does.
_REFUSAL_FINISHES = frozenset(
    {
        "SAFETY",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "RECITATION",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
    }
)

#: json_schema_for() adds keys Gemini's structured-output validator rejects.
_STRIP_SCHEMA_KEYS = ("additionalProperties", "title", "$schema")

#: HTTP statuses worth retrying: rate limiting and transient server faults.
#: Gemini load-sheds generation with 503 "high demand" under load; the Anthropic
#: SDK retries these itself, so the graph never used to see them.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    """True for transient Gemini errors (rate limit / 5xx) worth another attempt."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in _RETRYABLE_STATUS


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip keys Gemini rejects from a JSON schema.

    Extra fields the model might still emit are harmless: the pydantic model the
    caller validates against ignores unknown keys by default.
    """
    clean = copy.deepcopy(schema)

    def scrub(node: Any) -> None:
        if isinstance(node, dict):
            for key in _STRIP_SCHEMA_KEYS:
                node.pop(key, None)
            for value in node.values():
                scrub(value)
        elif isinstance(node, list):
            for item in node:
                scrub(item)

    scrub(clean)
    return clean


class GeminiClient(LLMClient):
    """Drop-in LLMClient backed by the google-genai SDK."""

    def __init__(self, settings: Settings | None = None) -> None:
        # Deliberately does NOT call super().__init__ - that builds an Anthropic
        # client and would fail without an Anthropic credential.
        from google import genai

        from advanced_rag.certs import enable_system_trust_store

        # Inject the OS trust store BEFORE constructing the client: httpx builds
        # its SSL context at construction time, so a client built first (e.g. a
        # guardrail call before any embedding) would bake in the certifi bundle
        # and never verify a corporate CA. Idempotent; safe to call every time.
        enable_system_trust_store()
        self.settings = settings or get_settings()
        # api_key=None lets the SDK fall back to GOOGLE_API_KEY / GEMINI_API_KEY.
        self._client = genai.Client(api_key=self.settings.google_api_key or None)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        cache_system: bool = True,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        thinking: bool = True,
        history: list[dict[str, Any]] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        from google.genai import types

        chosen_model = model or self.settings.llm_model
        cfg: dict[str, Any] = {"max_output_tokens": max_tokens or self.settings.llm_max_tokens}
        if system:
            cfg["system_instruction"] = system
        if output_schema:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = _gemini_schema(output_schema)

        contents = self._contents(prompt, history)
        config = types.GenerateContentConfig(**cfg)
        from google.genai import errors

        try:
            response = self._generate(model=chosen_model, contents=contents, config=config)
        except errors.APIError as exc:
            # Last resort when the primary model is being load-shed: one shot on a
            # configured fallback (typically the fast model) before giving up.
            fallback = self.settings.llm_retry_fallback_model
            if fallback and fallback != chosen_model and _is_retryable(exc):
                logger.warning(
                    "Primary model %s still failing (%s); one attempt on fallback %s",
                    chosen_model, getattr(exc, "code", None), fallback,
                )
                response = self._generate(model=fallback, contents=contents, config=config)
                chosen_model = fallback
            else:
                raise
        return self._to_result(response, chosen_model)

    def _generate(self, **kwargs: Any) -> Any:
        """generate_content with bounded exponential backoff on 429 / 5xx.

        Matches the Anthropic SDK's built-in retry so a transient 503 "high
        demand" degrades into a slightly slower answer instead of a failed
        generation and the graph's extractive-fallback notice. Depth is
        `LLM_MAX_RETRIES` (1 disables retrying).
        """
        import time

        from google.genai import errors

        attempts = max(1, self.settings.llm_max_retries)
        delay = 2.0
        for attempt in range(1, attempts + 1):
            try:
                return self._client.models.generate_content(**kwargs)
            except errors.APIError as exc:
                if not _is_retryable(exc) or attempt == attempts:
                    raise
                logger.warning(
                    "Gemini %s on generate (attempt %d/%d), retrying in %.0fs",
                    getattr(exc, "code", None) or getattr(exc, "status_code", None),
                    attempt, attempts, delay,
                )
                time.sleep(delay)
                delay *= 2

    def count_tokens(self, prompt: str, *, system: str | None = None) -> int:
        contents = [system, prompt] if system else prompt
        response = self._client.models.count_tokens(model=self.settings.llm_model, contents=contents)
        return int(response.total_tokens or 0)

    # ----------------------------------------------------------------- private

    @staticmethod
    def _contents(prompt: str, history: list[dict[str, Any]] | None) -> list[Any]:
        from google.genai import types

        contents: list[Any] = []
        for message in history or []:
            role = "model" if message.get("role") == "assistant" else "user"
            text = message.get("content")
            if not isinstance(text, str):
                text = str(text)
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=prompt)]))
        return contents

    @staticmethod
    def _to_result(response: Any, model: str) -> LLMResult:
        finish: str | None = None
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            raw = getattr(candidates[0], "finish_reason", None)
            finish = getattr(raw, "name", None) or (str(raw) if raw is not None else None)

        try:
            text = response.text or ""
        except Exception:  # .text can raise when the response was blocked
            text = ""

        if finish in _REFUSAL_FINISHES:
            stop_reason = "refusal"
        elif finish == "MAX_TOKENS":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=text.strip(),
            model=getattr(response, "model_version", None) or model,
            stop_reason=stop_reason,
            refusal_category=finish if stop_reason == "refusal" else None,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            cache_read_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        )
