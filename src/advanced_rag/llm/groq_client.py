"""Groq API client implementation adhering to the common LLMClient interface.

Provides fast hosted LLM inference on Groq's LPU infrastructure, supporting both
free-form text completions and structured JSON schema outputs with bounded
retries and rate-limit backoff.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from advanced_rag.config import Settings, get_settings
from advanced_rag.llm.client import (
    LLMClient,
    LLMRefusedError,
    LLMResult,
    LLMUnavailableError,
    json_schema_for,
)
from advanced_rag.llm.json_repair import parse_and_validate_json

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    """Detect transient 429 rate limits or 5xx server errors from Groq API."""
    import groq

    if isinstance(exc, (groq.RateLimitError, groq.InternalServerError)):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in _RETRYABLE_STATUS


class GroqClient(LLMClient):
    """Drop-in LLMClient backed by the official Groq SDK."""

    provider = "groq"

    def __init__(self, settings: Settings | None = None) -> None:
        # Deliberately do NOT call super().__init__() to avoid Anthropic client initialization.
        self.settings = settings or get_settings()
        api_key = self.settings.groq_api_key
        if not api_key:
            # We allow creation for inspection/testing, but complete() will enforce key presence.
            logger.debug("GroqClient initialized without explicit GROQ_API_KEY")

        from advanced_rag.certs import enable_system_trust_store

        enable_system_trust_store()

        from groq import Groq

        self._client = Groq(
            api_key=api_key or "gsk_dummy_placeholder",
            timeout=self.settings.llm_timeout_seconds,
        )

    def _ensure_api_key(self) -> None:
        if not self.settings.groq_api_key:
            raise LLMUnavailableError(
                "GROQ_API_KEY is not configured. Set GROQ_API_KEY in your environment or .env."
            )

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
        self._ensure_api_key()
        chosen_model = model or self.settings.groq_model

        messages: list[dict[str, Any]] = []

        # Groq requires the word 'json' in messages when response_format={"type": "json_object"}
        effective_system = system or ""
        if output_schema:
            schema_str = json.dumps(output_schema)
            json_instruction = (
                f"\nYou must respond with a valid JSON object strictly adhering to this schema:\n{schema_str}"
            )
            if effective_system:
                effective_system = f"{effective_system}\n{json_instruction}"
            else:
                effective_system = (
                    f"You are a helpful IT operations assistant.{json_instruction}"
                )

        estimated_in = self.count_tokens(prompt, system=effective_system)
        if estimated_in > self.settings.llm_max_input_tokens:
            logger.warning(
                "Prompt length (%d estimated tokens) exceeds configured ceiling (%d tokens).",
                estimated_in,
                self.settings.llm_max_input_tokens,
            )

        if effective_system:
            messages.append({"role": "system", "content": effective_system})

        for item in history or []:
            role = item.get("role", "user")
            content = item.get("content", "")
            messages.append({"role": role, "content": str(content)})

        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "temperature": 0.0,
        }

        if output_schema:
            kwargs["response_format"] = {"type": "json_object"}

        start_time = time.perf_counter()
        response = self._generate_with_retry(kwargs, chosen_model)
        latency = time.perf_counter() - start_time

        result = self._to_result(response, chosen_model)
        logger.info(
            "Groq inference: model=%s, in_tokens=%d, out_tokens=%d, latency=%.2fs",
            chosen_model,
            result.input_tokens,
            result.output_tokens,
            latency,
        )
        return result

    def _generate_with_retry(self, kwargs: dict[str, Any], primary_model: str) -> Any:
        """Call Groq API with bounded exponential backoff on transient errors."""
        import groq

        attempts = max(1, self.settings.llm_max_retries)
        delay = 2.0

        for attempt in range(1, attempts + 1):
            try:
                return self._client.chat.completions.create(**kwargs)
            except groq.APIError as exc:
                if not _is_retryable(exc) or attempt == attempts:
                    # Check if a fallback model is configured
                    fallback = self.settings.llm_retry_fallback_model or self.settings.groq_fast_model
                    if fallback and fallback != primary_model and _is_retryable(exc):
                        logger.warning(
                            "Primary Groq model %s failed (%s); retrying with fallback model %s",
                            primary_model,
                            getattr(exc, "status_code", None),
                            fallback,
                        )
                        fallback_kwargs = dict(kwargs)
                        fallback_kwargs["model"] = fallback
                        try:
                            return self._client.chat.completions.create(**fallback_kwargs)
                        except Exception as fb_exc:
                            logger.error("Fallback Groq model %s also failed: %s", fallback, fb_exc)
                            raise exc from fb_exc
                    raise

                logger.warning(
                    "Groq API %s (attempt %d/%d), retrying in %.1fs: %s",
                    getattr(exc, "status_code", "error"),
                    attempt,
                    attempts,
                    delay,
                    exc,
                )
                time.sleep(delay)
                delay *= 2.0

    def complete_json(
        self,
        prompt: str,
        schema_model: type[T],
        *,
        system: str | None = None,
        model: str | None = None,
        effort: str = "low",
        max_tokens: int = 2_000,
    ) -> T:
        """Completion constrained to schema_model with bounded validation retries."""
        schema = json_schema_for(schema_model)
        chosen_model = model or self.settings.groq_fast_model

        # Initial attempt
        result = self.complete(
            prompt,
            system=system,
            model=chosen_model,
            max_tokens=max_tokens,
            output_schema=schema,
        )
        if result.refused:
            raise LLMRefusedError(result.refusal_category)

        try:
            return parse_and_validate_json(result.text, schema_model)
        except (ValidationError, Exception) as parse_exc:
            logger.warning(
                "Initial Groq JSON validation failed: %s. Retrying with explicit correction...",
                parse_exc,
            )

        # Retry once with explicit feedback if first attempt returned invalid JSON
        repair_prompt = (
            f"{prompt}\n\nYour previous response failed validation:\n"
            f"Error: {parse_exc}\n"
            f"Invalid Output:\n{result.text}\n\n"
            "Please return ONLY a valid JSON object matching the required schema."
        )
        retry_result = self.complete(
            repair_prompt,
            system=system,
            model=chosen_model,
            max_tokens=max_tokens,
            output_schema=schema,
        )
        return parse_and_validate_json(retry_result.text, schema_model)

    def count_tokens(self, prompt: str, *, system: str | None = None) -> int:
        """Fast offline token count estimation (~4 characters per token)."""
        total_chars = len(prompt) + (len(system) if system else 0)
        return max(1, total_chars // 4)

    @staticmethod
    def _to_result(response: Any, model: str) -> LLMResult:
        choice = response.choices[0]
        text = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None) or "end_turn"

        if finish_reason in ("refusal", "content_filter"):
            stop_reason = "refusal"
        elif finish_reason == "length":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        usage = getattr(response, "usage", None)
        return LLMResult(
            text=text.strip(),
            model=getattr(response, "model", None) or model,
            stop_reason=stop_reason,
            refusal_category="safety" if stop_reason == "refusal" else None,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cache_read_tokens=0,
        )

