"""LLM client tests.

No network: a fake SDK surface captures the request parameters, which is exactly
what needs asserting — sending `output_config.effort` or adaptive thinking to a
model that predates them is a 400, and the grader nodes run on a cheap model.
"""

from __future__ import annotations

import anthropic
import pytest
from pydantic import BaseModel, Field

from advanced_rag.config import Settings
from advanced_rag.llm.client import (
    LLMClient,
    json_schema_for,
    stable_json,
    supports_effort,
    supports_fallbacks,
)


class Usage:
    input_tokens = 11
    output_tokens = 22
    cache_read_input_tokens = 5


class TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeResponse:
    model = "fake-model"
    stop_reason = "end_turn"
    stop_details = None
    usage = Usage()

    def __init__(self, text: str = '{"ok": true}') -> None:
        self.content = [TextBlock(text)]


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.calls: list[dict] = []
        self._response = response or FakeResponse()
        self._error = error

    def create(self, **params):
        self.calls.append(params)
        if self._error is not None:
            raise self._error
        return self._response


class FakeClient:
    """Stands in for anthropic.Anthropic with both the stable and beta surfaces."""

    def __init__(self, beta_error=None):
        self.messages = FakeMessages()

        class Beta:
            pass

        self.beta = Beta()
        self.beta.messages = FakeMessages(error=beta_error)


@pytest.fixture
def client(monkeypatch) -> LLMClient:
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeClient())
    return LLMClient(settings=Settings(llm_model="claude-opus-5", llm_refusal_fallbacks=False))


def last_call(client: LLMClient) -> dict:
    return client._client.messages.calls[-1]


def bad_request(message: str) -> anthropic.BadRequestError:
    """The SDK's exceptions need a real response object to construct."""
    # anthropic 1.x is built on httpx2, not httpx - the types are not interchangeable.
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        message=message, response=httpx2.Response(400, request=request), body=None
    )


# ------------------------------------------------------------- capabilities


def test_capability_tables():
    assert supports_effort("claude-opus-5")
    assert supports_effort("claude-sonnet-5")
    assert not supports_effort("claude-haiku-4-5")
    assert supports_fallbacks("claude-opus-5")
    assert not supports_fallbacks("claude-sonnet-5")
    assert not supports_fallbacks("claude-haiku-4-5")


def test_modern_model_gets_effort_and_adaptive_thinking(client):
    client.complete("hi", effort="medium")
    params = last_call(client)
    assert params["output_config"]["effort"] == "medium"
    assert params["thinking"] == {"type": "adaptive"}


def test_older_model_gets_neither(client):
    """Haiku 4.5 rejects both; the request must not carry them."""
    client.complete("hi", model="claude-haiku-4-5")
    params = last_call(client)
    assert "thinking" not in params
    assert "output_config" not in params


def test_older_model_still_gets_json_schema(client):
    client.complete("hi", model="claude-haiku-4-5", output_schema={"type": "object"})
    params = last_call(client)
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert "effort" not in params["output_config"]
    assert "thinking" not in params


# ------------------------------------------------------------------ caching


def test_system_prompt_is_cached_by_default(client):
    client.complete("hi", system="frozen instructions")
    block = last_call(client)["system"][0]
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"] == "frozen instructions"


def test_system_caching_can_be_disabled(client):
    client.complete("hi", system="volatile", cache_system=False)
    assert "cache_control" not in last_call(client)["system"][0]


def test_usage_is_reported(client):
    result = client.complete("hi")
    assert (result.input_tokens, result.output_tokens) == (11, 22)
    assert result.cache_read_tokens == 5


# ---------------------------------------------------------------- fallbacks


def test_fallbacks_are_attached_for_supported_models(monkeypatch):
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeClient())
    llm = LLMClient(settings=Settings(llm_model="claude-opus-5", llm_refusal_fallbacks=True))
    llm.complete("hi")
    params = llm._client.beta.messages.calls[-1]
    assert params["fallbacks"] == "default"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert llm._client.messages.calls == []


def test_fallbacks_are_skipped_for_unsupported_models(monkeypatch):
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeClient())
    llm = LLMClient(settings=Settings(llm_refusal_fallbacks=True))
    llm.complete("hi", model="claude-haiku-4-5")
    assert llm._client.beta.messages.calls == []
    assert len(llm._client.messages.calls) == 1


def test_fallback_rejection_disables_the_beta_and_retries(monkeypatch):
    error = bad_request("fallbacks beta is not enabled for this organization")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeClient(beta_error=error))
    llm = LLMClient(settings=Settings(llm_model="claude-opus-5", llm_refusal_fallbacks=True))

    llm.complete("hi")
    assert len(llm._client.messages.calls) == 1, "should retry on the stable endpoint"
    assert llm._fallbacks_enabled is False

    # Second call must not pay for the failing beta attempt again.
    llm.complete("hi again")
    assert len(llm._client.beta.messages.calls) == 1
    assert len(llm._client.messages.calls) == 2


def test_unrelated_bad_request_is_not_swallowed(monkeypatch):
    error = bad_request("max_tokens must be positive")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeClient(beta_error=error))
    llm = LLMClient(settings=Settings(llm_model="claude-opus-5", llm_refusal_fallbacks=True))
    with pytest.raises(anthropic.BadRequestError):
        llm.complete("hi")


# -------------------------------------------------------------- json schema


class Grade(BaseModel):
    verdict: str = Field(description="the call")
    confident: bool = Field(description="whether it is sure")


def test_json_schema_is_closed_and_fully_required():
    schema = json_schema_for(Grade)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"verdict", "confident"}


def test_complete_json_validates_the_response(monkeypatch):
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeClient())
    llm = LLMClient(settings=Settings(llm_refusal_fallbacks=False))
    llm._client.messages._response = FakeResponse('{"verdict": "correct", "confident": true}')

    grade = llm.complete_json("grade this", Grade)
    assert grade.verdict == "correct"
    assert grade.confident is True


def test_stable_json_is_key_sorted():
    assert stable_json({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
