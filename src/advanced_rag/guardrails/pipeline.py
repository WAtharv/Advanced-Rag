"""The nine-layer guardrails pipeline.

Layers are ordered by cost on purpose: six deterministic checks run before any
token is spent, and the two LLM-backed layers only see input that survived them.

    Inbound                                     Outbound
    1. shape          (size, emptiness)          7. destructive_output
    2. pii_redaction  (redact, allow)            8. secret_egress
    3. secret_request (block)                    9. output_review (LLM)
    4. injection      (block)
    5. intent         (LLM, block)
    6. scope          (LLM, block)

Every layer returns a GuardrailOutcome, so a blocked request can explain itself
and the UI can show which layer fired.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from advanced_rag.config import Settings, get_settings
from advanced_rag.guardrails import patterns
from advanced_rag.llm import prompts
from advanced_rag.llm.client import LLMClient, get_llm, llm_available
from advanced_rag.models import GuardrailOutcome

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 4_000
REFUSAL_MESSAGE = (
    "I can't help with that request. It was stopped by the {layer} guardrail: {detail}"
)


class IntentVerdict(BaseModel):
    violates_policy: bool = Field(description="true if the request breaches platform policy")
    category: str = Field(description="short label, or 'none'")
    reason: str = Field(description="one sentence")


class ScopeVerdict(BaseModel):
    in_scope: bool = Field(description="true if about this Kubernetes platform")
    reason: str = Field(description="one sentence")


class OutputVerdict(BaseModel):
    safe: bool = Field(description="true if the answer may be shown as written")
    issue: str = Field(description="what to fix, or 'none'")


class GuardrailResult(BaseModel):
    """Aggregate decision for one direction of one request."""

    outcomes: list[GuardrailOutcome] = Field(default_factory=list)
    text: str = ""
    blocked: bool = False
    message: str = ""

    def record(self, outcome: GuardrailOutcome) -> None:
        self.outcomes.append(outcome)
        if outcome.action == "block":
            self.blocked = True
            self.message = REFUSAL_MESSAGE.format(layer=outcome.layer, detail=outcome.detail)


class Guardrails:
    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm = llm

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    # ------------------------------------------------------------- inbound

    def check_input(self, question: str, *, use_llm: bool = True) -> GuardrailResult:
        result = GuardrailResult(text=question)
        if not self.settings.enable_guardrails:
            return result

        # Layer 1 - shape.
        stripped = question.strip()
        if not stripped:
            result.record(_block("shape", "the question is empty"))
            return result
        if len(stripped) > MAX_QUESTION_CHARS:
            result.record(
                _block("shape", f"the question exceeds {MAX_QUESTION_CHARS} characters")
            )
            return result
        result.record(GuardrailOutcome(layer="shape", passed=True, detail="length ok"))

        # Layer 2 - PII redaction. Non-blocking: an engineer pasting a pod IP into
        # a question is normal, but the value should not reach the model or logs.
        redacted, pii_hits = patterns.redact(patterns.PII_PATTERNS, stripped)
        result.text = redacted
        result.record(
            GuardrailOutcome(
                layer="pii_redaction",
                passed=True,
                action="redact" if pii_hits else "allow",
                detail=("redacted " + ", ".join(pii_hits)) if pii_hits else "no PII found",
            )
        )

        # Layer 3 - requests for secret material.
        if patterns.matches_any(patterns.SECRET_REQUEST_PATTERNS, stripped):
            result.record(
                _block("secret_request", "requests for secret values require break-glass access")
            )
            return result
        result.record(GuardrailOutcome(layer="secret_request", passed=True))

        # Layer 4 - prompt injection.
        if patterns.matches_any(patterns.INJECTION_PATTERNS, stripped):
            result.record(
                _block("injection", "the request tries to override the assistant's instructions")
            )
            return result
        result.record(GuardrailOutcome(layer="injection", passed=True))

        if not use_llm:
            return result

        # Layers 5 and 6 are the only two independent model calls in a request -
        # intent and scope both judge the same redacted question and neither reads
        # the other's verdict. Run them concurrently so the request waits one
        # round trip instead of two; the outcomes are still recorded in layer
        # order below, and an intent block still suppresses the scope outcome
        # exactly as the sequential version did (the extra scope call it wastes on
        # a block is cheap and rare).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            intent_future = pool.submit(
                self._classify, IntentVerdict, prompts.GUARDRAIL_INTENT_SYSTEM, redacted, "intent"
            )
            scope_future = pool.submit(
                self._classify, ScopeVerdict, _SCOPE_SYSTEM, redacted, "scope"
            )
            verdict = intent_future.result()
            scope = scope_future.result()

        # Layer 5 - intent classification.
        if verdict is None:
            result.record(_skipped("intent", _skip_reason()))
        elif verdict.violates_policy:
            result.record(_block("intent", verdict.reason or verdict.category))
            return result
        else:
            result.record(GuardrailOutcome(layer="intent", passed=True, detail="allowed"))

        # Layer 6 - topical scope.
        if scope is None:
            result.record(_skipped("scope", _skip_reason()))
        elif not scope.in_scope:
            result.record(_block("scope", scope.reason or "outside the platform's remit"))
        else:
            result.record(GuardrailOutcome(layer="scope", passed=True, detail="in scope"))
        return result

    # ------------------------------------------------------------ outbound

    def check_output(
        self, answer: str, *, question: str = "", context: str = "", use_llm: bool = True
    ) -> GuardrailResult:
        result = GuardrailResult(text=answer)
        if not self.settings.enable_guardrails:
            return result

        # Layer 7 - destructive operations in generated text. These are allowed,
        # but only when the answer says what they will do first.
        destructive = patterns.find(patterns.DESTRUCTIVE_PATTERNS, answer)
        if destructive:
            annotated = _needs_approval_notice(answer)
            result.text = annotated
            result.record(
                GuardrailOutcome(
                    layer="destructive_output",
                    passed=True,
                    action="redact",
                    detail="flagged " + ", ".join(destructive) + " as approval-gated",
                )
            )
        else:
            result.record(GuardrailOutcome(layer="destructive_output", passed=True))

        # Layer 8 - secret egress. Redact rather than block: the useful part of
        # the answer usually survives losing the value.
        scrubbed, secrets = patterns.redact(patterns.SECRET_PATTERNS, result.text)
        result.text = scrubbed
        result.record(
            GuardrailOutcome(
                layer="secret_egress",
                passed=True,
                action="redact" if secrets else "allow",
                detail=("redacted " + ", ".join(secrets)) if secrets else "no secrets found",
            )
        )

        if not use_llm:
            return result

        # Layer 9 - LLM review for grounding and unflagged danger.
        prompt = (
            "Question:\n" + question + "\n\nContext given to the assistant:\n"
            + (context or "(none)") + "\n\nDraft answer:\n" + result.text
        )
        verdict = self._classify(OutputVerdict, prompts.OUTPUT_SAFETY_SYSTEM, prompt, "output")
        if verdict is None:
            result.record(_skipped("output_review", _skip_reason()))
        elif not verdict.safe:
            result.record(_block("output_review", verdict.issue or "failed safety review"))
        else:
            result.record(GuardrailOutcome(layer="output_review", passed=True, detail="approved"))
        return result

    # -------------------------------------------------------------- private

    def _classify(self, schema, system: str, text: str, label: str):
        """Run one classifier; a failure must not take the request down with it."""
        if not llm_available():
            # No model configured. Skipping quietly is right - but the
            # outcome is still recorded as a skip, so the answer is never
            # presented as having passed nine layers when it saw six.
            return None
        try:
            return self.llm.complete_json(text, schema, system=system, effort="low")
        except Exception:
            logger.exception("Guardrail layer %s failed open", label)
            return None


_SCOPE_SYSTEM = """You decide whether a question belongs to a Kubernetes platform
operations assistant.

In scope: Kubernetes, containers, cluster networking, storage, observability,
CI/CD and deployments, incident response, platform policy, and the operations
database that records incidents, deployments, clusters and nodes.

Out of scope: general knowledge, entertainment, personal advice, other companies'
systems, and coding help unrelated to this platform.

Borderline questions about adjacent infrastructure are in scope. Judge the
subject, not the phrasing."""


def _skip_reason() -> str:
    """Why an LLM-backed layer did not run.

    A missing key and a classifier outage both fail open, but they are not the
    same fact and the reader acts differently on each.
    """
    return "no LLM configured (offline mode)" if not llm_available() else "classifier unavailable"


def _block(layer: str, detail: str) -> GuardrailOutcome:
    logger.warning("Guardrail %s blocked the request: %s", layer, detail)
    return GuardrailOutcome(layer=layer, passed=False, action="block", detail=detail)


def _skipped(layer: str, detail: str) -> GuardrailOutcome:
    """A layer that failed open. Reported as skipped, never as passed."""
    logger.warning("Guardrail %s did not run: %s", layer, detail)
    return GuardrailOutcome(layer=layer, passed=False, action="skip", detail=detail)


_APPROVAL_NOTICE = (
    "\n\n> **Change control:** the commands above mutate cluster state. Per the "
    "production change control policy they require approval before execution."
)


def _needs_approval_notice(answer: str) -> str:
    if "change control" in answer.lower():
        return answer
    return answer.rstrip() + _APPROVAL_NOTICE


_guardrails: Guardrails | None = None


def get_guardrails() -> Guardrails:
    global _guardrails
    if _guardrails is None:
        _guardrails = Guardrails()
    return _guardrails
