"""Ollama local LLM client implementation adhering to the common LLMClient interface.

Provides fully local, self-hosted LLM inference via Ollama's HTTP API.
No external network access or cloud API keys required.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

import httpx
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


class OllamaClient(LLMClient):
    """Drop-in LLMClient backed by a local Ollama server instance."""

    provider = "ollama"

    def __init__(self, settings: Settings | None = None) -> None:
        # Deliberately do NOT call super().__init__() to avoid Anthropic client initialization.
        self.settings = settings or get_settings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.timeout = self.settings.ollama_timeout_seconds
        self.default_model = self.settings.ollama_model

    def is_available(self) -> bool:
        """Health check: probe Ollama server with a fast ping."""
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.get(f"{self.base_url}/api/version")
                return res.status_code == 200
        except (httpx.RequestError, httpx.TimeoutException):
            return False

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
        chosen_model = model or self.default_model
        messages: list[dict[str, Any]] = []

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

        if effective_system:
            messages.append({"role": "system", "content": effective_system})

        for item in history or []:
            role = item.get("role", "user")
            content = item.get("content", "")
            messages.append({"role": role, "content": str(content)})

        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.0,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        if output_schema:
            payload["format"] = "json"

        endpoint = f"{self.base_url}/api/chat"

        import time

        start_time = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(endpoint, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            logger.error("Could not connect to Ollama at %s: %s", self.base_url, exc)
            raise LLMUnavailableError(
                f"Ollama server is unreachable at {self.base_url}. Ensure Ollama is running (`ollama serve`)."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama request timed out after %.1fs: %s", self.timeout, exc)
            raise LLMUnavailableError(f"Ollama request timed out ({self.timeout}s).") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama HTTP %d error: %s", exc.response.status_code, exc.response.text)
            raise RuntimeError(f"Ollama HTTP {exc.response.status_code}: {exc.response.text}") from exc

        latency = time.perf_counter() - start_time
        result = self._to_result(data, chosen_model)
        logger.info(
            "Ollama inference: model=%s, in_tokens=%d, out_tokens=%d, latency=%.2fs",
            chosen_model,
            result.input_tokens,
            result.output_tokens,
            latency,
        )
        return result

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
        chosen_model = model or self.default_model

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
                "Initial Ollama JSON validation failed: %s. Retrying with explicit correction...",
                parse_exc,
            )

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
    def _to_result(data: dict[str, Any], model: str) -> LLMResult:
        message = data.get("message") or {}
        text = message.get("content", "")
        done = data.get("done", True)

        return LLMResult(
            text=text.strip(),
            model=data.get("model", model),
            stop_reason="end_turn" if done else "unknown",
            refusal_category=None,
            input_tokens=data.get("prompt_eval_count", 0) or 0,
            output_tokens=data.get("eval_count", 0) or 0,
            cache_read_tokens=0,
        )

