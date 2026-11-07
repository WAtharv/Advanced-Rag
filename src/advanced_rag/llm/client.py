"""Thin wrapper over the Anthropic SDK.

Centralises the choices that would otherwise be repeated at every call site:
adaptive thinking, the effort knob, prompt caching of stable system prompts,
server-side refusal fallbacks, and structured (JSON-schema) responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]
from pydantic import BaseModel

from advanced_rag.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Beta flag for the scalar `fallbacks: "default"` form (routes by refusal category).
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Models that accept `output_config.effort` and `thinking: {"type": "adaptive"}`.
#: Sending either to an older model (Haiku 4.5, Sonnet 4.5) is a 400, and the
#: grader nodes deliberately run on a cheap model - so capability is checked
#: rather than assumed.
_SUPPORTS_EFFORT_AND_ADAPTIVE = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
)

#: Models with server-side refusal fallbacks. Narrower than the list above.
_SUPPORTS_FALLBACKS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")

T = TypeVar("T", bound=BaseModel)


def supports_effort(model: str) -> bool:
    return model in _SUPPORTS_EFFORT_AND_ADAPTIVE


def supports_fallbacks(model: str) -> bool:
    return model in _SUPPORTS_FALLBACKS


class LLMResult(BaseModel):
    """Normalised view of a Messages API response."""

    text: str
    thinking: str = ""
    model: str = ""
    stop_reason: str | None = None
    refusal_category: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class LLMRefusedError(RuntimeError):
    """Raised when Claude declined the request and no fallback rescued it."""

    def __init__(self, category: str | None = None) -> None:
        self.category = category
        super().__init__("model refused the request (category=" + (category or "unknown") + ")")


class LLMClient:
    """Synchronous Claude client used by every node in the graph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if anthropic is None:
            raise LLMUnavailableError(
                "Anthropic SDK is not installed. Install with `pip install -e '.[anthropic]'`"
            )
        # Passing None lets the SDK resolve ANTHROPIC_API_KEY / an auth profile itself.
        self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key or None)
        self._fallbacks_enabled = self.settings.llm_refusal_fallbacks

    # ------------------------------------------------------------------ public

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
        """Single-turn (or history-continuing) completion."""
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": prompt})

        chosen_model = model or self.settings.llm_model
        params: dict[str, Any] = {
            "model": chosen_model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "messages": messages,
            "output_config": {},
        }
        if system:
            params["system"] = self._system_blocks(system, cache=cache_system)

        if supports_effort(chosen_model):
            params["output_config"]["effort"] = effort or self.settings.llm_effort
            if thinking:
                params["thinking"] = {"type": "adaptive"}
        elif thinking:
            # Older models take a fixed budget instead of adaptive thinking. The
            # grader nodes do not need reasoning, so the cheaper choice is to
            # simply leave thinking off rather than reserve a budget.
            logger.debug("%s predates adaptive thinking - running without it", chosen_model)

        if output_schema:
            # A JSON-schema format guarantees the first text block parses as JSON.
            params["output_config"]["format"] = {
                "type": "json_schema",
                "schema": output_schema,
            }
        if not params["output_config"]:
            del params["output_config"]

        return self._to_result(self._send(params))

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
        """Completion constrained to schema_model, returned as a validated model.

        Used by the grader nodes (router, CRAG relevance, Self-RAG critique,
        guardrail classifiers) where a free-text answer would need parsing.
        """
        result = self.complete(
            prompt,
            system=system,
            model=model or self.settings.llm_fast_model,
            effort=effort,
            max_tokens=max_tokens,
            output_schema=json_schema_for(schema_model),
        )
        if result.refused:
            raise LLMRefusedError(result.refusal_category)
        from advanced_rag.llm.json_repair import parse_and_validate_json

        return parse_and_validate_json(result.text, schema_model)

    def count_tokens(self, prompt: str, *, system: str | None = None) -> int:
        response = self._client.messages.count_tokens(
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            **({"system": system} if system else {}),
        )
        return response.input_tokens

    # ----------------------------------------------------------------- private

    @staticmethod
    def _system_blocks(system: str, *, cache: bool) -> list[dict[str, Any]]:
        block: dict[str, Any] = {"type": "text", "text": system}
        if cache:
            # System prompts here are frozen strings, so the prefix is stable and
            # the cache actually hits across requests.
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _send(self, params: dict[str, Any]) -> Any:
        """Issue the request, using server-side refusal fallbacks when available."""
        if self._fallbacks_enabled and supports_fallbacks(params["model"]):
            try:
                return self._client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **params
                )
            except anthropic.BadRequestError as exc:
                if not _is_fallback_rejection(exc):
                    raise
                # Org/model not entitled to the beta - stop paying the round trip.
                logger.warning("Refusal fallbacks unavailable, disabling: %s", exc.message)
                self._fallbacks_enabled = False
        return self._client.messages.create(**params)

    @staticmethod
    def _to_result(response: Any) -> LLMResult:
        text = "".join(b.text for b in response.content if b.type == "text")
        thinking = "".join(
            getattr(b, "thinking", "") or "" for b in response.content if b.type == "thinking"
        )
        details = getattr(response, "stop_details", None)
        usage = response.usage
        return LLMResult(
            text=text.strip(),
            thinking=thinking.strip(),
            model=response.model,
            stop_reason=response.stop_reason,
            refusal_category=getattr(details, "category", None) if details else None,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )


def _is_fallback_rejection(exc: Any) -> bool:
    message = (getattr(exc, "message", None) or str(exc)).lower()
    return "fallback" in message or "beta" in message


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema tightened to what the API's strict mode requires."""
    schema = model.model_json_schema()
    _require_all(schema)
    return schema


def _require_all(node: Any) -> None:
    """Mark every object node closed and all of its properties required."""
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())
    for value in node.values():
        if isinstance(value, dict):
            _require_all(value)
        elif isinstance(value, list):
            for item in value:
                _require_all(item)


def stable_json(value: Any) -> str:
    """Deterministic JSON - keeps prompt prefixes cache-stable."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class LLMUnavailableError(RuntimeError):
    """No LLM backend is configured, so no model call can be made."""


_singleton: LLMClient | None = None
#: Tri-state: None = not probed yet, True/False = latched answer from `llm_available`.
_available: bool | None = None


def get_llm() -> LLMClient:
    """Process-wide client (the SDK is thread-safe and pools connections)."""
    global _singleton
    if _singleton is None:
        settings = get_settings()
        logger.info("Initializing LLM provider: %s", settings.llm_provider)
        if settings.llm_provider == "offline":
            raise LLMUnavailableError("LLM_PROVIDER=offline - no model backend configured")
        if settings.llm_provider == "groq":
            from advanced_rag.llm.groq_client import GroqClient

            _singleton = GroqClient(settings)
        elif settings.llm_provider == "ollama":
            from advanced_rag.llm.ollama_client import OllamaClient

            _singleton = OllamaClient(settings)
        elif settings.llm_provider == "gemini":
            from advanced_rag.llm.gemini_client import GeminiClient

            _singleton = GeminiClient(settings)
        elif settings.llm_provider == "anthropic":
            _singleton = LLMClient(settings)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
    return _singleton


def llm_available() -> bool:
    """Whether a model call can actually be made.

    Probed by checking provider requirements or pinging the local server once,
    then latched.
    """
    global _available
    if _available is None:
        settings = get_settings()
        if settings.llm_provider == "offline":
            _available = False
            return False

        try:
            client = get_llm()
            if settings.llm_provider == "groq":
                _available = bool(settings.groq_api_key)
            elif settings.llm_provider == "ollama":
                from advanced_rag.llm.ollama_client import OllamaClient

                if isinstance(client, OllamaClient):
                    _available = client.is_available()
                else:
                    _available = True
            elif settings.llm_provider == "gemini":
                _available = bool(settings.google_api_key)
            else:
                _available = True
        except Exception as exc:
            logger.warning(
                "No LLM backend available for provider %s (%s: %s) - running in offline mode, "
                "answers will be extractive",
                settings.llm_provider,
                type(exc).__name__,
                exc,
            )
            _available = False
    return _available


def reset_llm() -> None:
    """Drop the cached client and availability probe (tests, config reloads)."""
    global _singleton, _available
    _singleton = None
    _available = None
