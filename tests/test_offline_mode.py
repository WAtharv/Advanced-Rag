"""Offline mode: the pipeline with no LLM credentials at all.

Retrieval needs no key, so the useful half of the product still works. These
tests pin the two things that make that honest rather than merely quiet: the
answer says it is quoted rather than generated, and every LLM-backed layer
reports that it did not run.
"""

from __future__ import annotations

import pytest

from advanced_rag.config import Settings, get_settings
from advanced_rag.graph import nodes
from advanced_rag.llm.client import llm_available, reset_llm
from advanced_rag.llm.extractive import (
    MAX_PASSAGE_CHARS,
    MAX_PASSAGES,
    extractive_answer,
)
from advanced_rag.models import Chunk, RetrievedChunk, Verdict


@pytest.fixture
def offline(monkeypatch):
    """Select offline mode the way a user without a key would."""
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    get_settings.cache_clear()
    reset_llm()
    yield
    get_settings.cache_clear()
    reset_llm()


def chunk(source="oomkilled.md", section="OOMKilled containers", text="A container is OOMKilled "
          "when it exceeds its memory limit.", title="Runbook - OOMKilled", score=11.9):
    return RetrievedChunk(
        chunk=Chunk(id=source + section, text=text, source=source, title=title, section=section),
        retrieval_score=score,
    )


# ------------------------------------------------------------------ detection


def test_offline_provider_reports_no_llm(offline):
    assert llm_available() is False


def test_configured_provider_reports_available():
    # conftest puts a placeholder ANTHROPIC_API_KEY in the environment, so the
    # client constructs and the probe should say yes.
    assert llm_available() is True


def test_availability_is_latched(offline, monkeypatch):
    assert llm_available() is False
    # Flipping the setting without resetting must not change the latched answer:
    # nodes rely on one consistent verdict for the whole process.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()
    assert llm_available() is False


# ----------------------------------------------------------------- extractive


def test_extractive_answer_quotes_and_cites():
    answer = extractive_answer([chunk()])
    assert "No language model is configured" in answer
    assert "**[1] Runbook - OOMKilled › OOMKilled containers**" in answer
    assert "> A container is OOMKilled when it exceeds its memory limit." in answer


def test_extractive_answer_never_claims_to_be_generated():
    answer = extractive_answer([chunk()])
    lowered = answer.lower()
    assert "extractive" in lowered
    # The whole point: it must not read as a synthesised answer.
    assert "quoted verbatim" in lowered


def test_extractive_answer_with_no_chunks_says_so():
    answer = extractive_answer([])
    assert "retrieval returned no passages" in answer
    # It should also explain *why* recall is worse offline, not just that it is.
    assert "no query rewriting or HyDE" in answer


def test_extractive_answer_reports_a_weak_match():
    answer = extractive_answer([chunk()], verdict=Verdict.INCORRECT)
    assert "weak match" in answer


def test_extractive_answer_caps_passages_but_admits_it():
    chunks = [chunk(section=f"s{i}") for i in range(MAX_PASSAGES + 3)]
    answer = extractive_answer(chunks)
    assert f"**[{MAX_PASSAGES}]" in answer
    assert f"**[{MAX_PASSAGES + 1}]" not in answer
    assert "3 further passage(s) matched" in answer


def test_extractive_answer_truncates_a_long_passage():
    answer = extractive_answer([chunk(text="word " * 900)])
    quoted = [line for line in answer.split("\n") if line.startswith("> ")][0]
    assert len(quoted) < MAX_PASSAGE_CHARS + 40
    assert quoted.endswith("[…]")


def test_extractive_answer_includes_executed_sql_rows():
    answer = extractive_answer([chunk()], sql="SELECT 1", sql_rows_text="cluster | n\nprod | 4")
    assert "SELECT 1" in answer
    assert "prod | 4" in answer


# ----------------------------------------------------------------- graph nodes


def test_route_node_forces_vector_offline(offline):
    patch = nodes.route_node({"question": "how many sev1 incidents?"})
    assert patch["route"].value == "vector"
    assert "offline mode" in patch["trace"][0].detail


def test_generate_node_is_extractive_and_cacheable_offline(offline):
    patch = nodes.generate_node(
        {"original_question": "why OOMKilled?", "context": "ctx", "chunks": [chunk()]}
    )
    assert patch["extractive"] is True
    # `generation_failed` suppresses caching. An extractive answer is a pure
    # function of the retrieved chunks, so it must not carry that flag.
    assert "generation_failed" not in patch
    assert "OOMKilled containers" in patch["answer"]
    assert "extractive" in patch["trace"][0].detail


def test_grade_node_accepts_context_unjudged_offline(offline):
    patch = nodes.grade_node({"original_question": "q", "context": "c", "chunks": [chunk()]})
    assert patch["verdict"] is Verdict.CORRECT
    assert "unjudged" in patch["verdict_reason"]


def test_critique_node_skips_offline(offline):
    patch = nodes.critique_node({"original_question": "q", "answer": "a", "context": "c"})
    assert patch["critique"] == ""
    assert "offline mode" in patch["trace"][0].detail


def test_extractive_answer_survives_the_cache_round_trip(offline):
    stored = {"answer": "quoted text", "route": "vector", "extractive": True, "citations": []}

    class Hit:
        def lookup(self, _question):
            return stored, "exact"

    import advanced_rag.graph.nodes as node_module

    original = node_module.get_cache
    node_module.get_cache = lambda: Hit()
    try:
        patch = node_module.cache_lookup_node({"original_question": "q"})
    finally:
        node_module.get_cache = original

    # A cache hit whose body says "extractive" must not report extractive=False;
    # the UI badge would then contradict the answer it sits under.
    assert patch["extractive"] is True


# ------------------------------------------ force_extractive (Without LLM toggle)
#
# The UI's "Without LLM" mode sets force_extractive on a run even when a model is
# configured. These pin that it takes the same deterministic path offline mode
# does, rather than quietly generating.


def test_force_extractive_generates_no_llm_answer_with_a_model_available(monkeypatch):
    monkeypatch.setattr(nodes, "llm_available", lambda: True)
    patch = nodes.generate_node(
        {
            "original_question": "why OOMKilled?",
            "context": "ctx",
            "chunks": [chunk()],
            "force_extractive": True,
        }
    )
    assert patch["extractive"] is True
    # Same reasoning as offline: nothing failed, so the answer stays cacheable.
    assert "generation_failed" not in patch
    assert "OOMKilled containers" in patch["answer"]


def test_force_extractive_forces_vector_route_with_a_model_available(monkeypatch):
    monkeypatch.setattr(nodes, "llm_available", lambda: True)
    patch = nodes.route_node({"question": "how many sev1 incidents?", "force_extractive": True})
    assert patch["route"].value == "vector"
    assert "no-LLM mode" in patch["trace"][0].detail


def test_force_extractive_ignores_a_generated_cache_hit(monkeypatch):
    # A cached *generated* answer must never be served to a Without-LLM request.
    monkeypatch.setattr(nodes, "llm_available", lambda: True)
    stored = {"answer": "generated prose", "route": "vector", "extractive": False, "citations": []}

    class Hit:
        def lookup(self, _question):
            return stored, "exact"

    monkeypatch.setattr(nodes, "get_cache", lambda: Hit())
    patch = nodes.cache_lookup_node({"original_question": "q", "force_extractive": True})
    assert patch["cached"] is False
    assert patch["cache_kind"] == "none"


# ------------------------------------------------------------------ guardrails


def test_guardrails_name_the_missing_key_not_an_outage(offline):
    from advanced_rag.guardrails.pipeline import Guardrails

    result = Guardrails(Settings()).check_input("why is my pod crashlooping?")
    skipped = [o for o in result.outcomes if o.action == "skip"]
    assert skipped, "the LLM-backed layers should report a skip, not a pass"
    assert all("no LLM configured" in o.detail for o in skipped)
    # Failing open must never be recorded as having passed.
    assert all(o.passed is False for o in skipped)


def test_retrieval_helpers_no_op_offline(offline):
    from advanced_rag.retrieval.retriever import Retriever

    retriever = Retriever(Settings(), store=object(), reranker=object())
    assert retriever.generate_hyde("q") is None
    assert retriever.rewrite_query("q") == []


def test_text2sql_states_why_it_cannot_run_offline(offline):
    from advanced_rag.text2sql.generator import SqlGenerator

    proposal = SqlGenerator(Settings()).generate("how many sev1 incidents?")
    assert proposal.sql == ""
    assert "needs a language model" in (proposal.error or "")
