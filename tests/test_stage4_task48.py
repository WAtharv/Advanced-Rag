"""Comprehensive tests for Stage 4 Task 48: Grounded Answer Reliability.

Covers all 18 requirements:
1. Relevant retrieved context.
2. Partially relevant context.
3. Irrelevant retrieved context.
4. Malformed CRAG grading response.
5. CRAG provider failure.
6. Grounded answer.
7. Partially grounded answer.
8. Unsupported answer claims.
9. Missing citation.
10. Fabricated citation identifier.
11. Citation-to-context mapping.
12. Successful answer regeneration.
13. Failed regeneration fallback.
14. Maximum one regeneration attempt.
15. Disabled Self-RAG behavior.
16. Disabled CRAG behavior.
17. Graph termination and fallback behavior.
18. API response compatibility.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from advanced_rag.config import Settings
from advanced_rag.graph import builder, nodes, pipeline
from advanced_rag.graph.builder import MAX_RETRIEVAL_ATTEMPTS, build_graph
from advanced_rag.graph.nodes import (
    ContextGrade,
    Critique,
    citations_from,
    grade_node,
    validate_citations,
)
from advanced_rag.graph.state import initial_state
from advanced_rag.models import (
    AnswerResponse,
    Chunk,
    Citation,
    GuardrailOutcome,
    RetrievedChunk,
    Route,
    Verdict,
)
from advanced_rag.retrieval.retriever import RetrievalResult


# =====================================================================
# Fixtures and Fakes
# =====================================================================


def _chunk(id: str, source: str, text: str = "text", section: str = "Sec") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(id=id, text=text, source=source, section=section, document_id=f"doc-{id}"),
        retrieval_score=0.85,
        rerank_score=0.90,
        rerank_is_authoritative=True,
    )


class Task48FakeLLM:
    def __init__(
        self,
        *,
        route="vector",
        verdict="correct",
        verdict_reason="context answers question",
        critique_grounded=True,
        critique_cited=True,
        critique_addresses=True,
        critique_citations_valid=True,
        critique_missing_cites=False,
        critique_unsupported=None,
        critique_fix="none",
        draft_answers=None,
    ):
        self.route = route
        self.verdict = verdict
        self.verdict_reason = verdict_reason
        self.critique_grounded = critique_grounded
        self.critique_cited = critique_cited
        self.critique_addresses = critique_addresses
        self.critique_citations_valid = critique_citations_valid
        self.critique_missing_cites = critique_missing_cites
        self.critique_unsupported = critique_unsupported or []
        self.critique_fix = critique_fix
        self.draft_answers = list(draft_answers or ["Answer grounded in [1]."])
        self.calls: list[str] = []

    def complete_json(self, prompt, schema_model, **kwargs):
        name = schema_model.__name__
        self.calls.append(name)
        if name == "RouteDecision":
            return schema_model(route=self.route, reason="test route")
        if name == "ContextGrade":
            return schema_model(verdict=self.verdict, reason=self.verdict_reason)
        if name == "Critique":
            return schema_model(
                grounded=self.critique_grounded,
                addresses_question=self.critique_addresses,
                cited=self.critique_cited,
                citations_valid=self.critique_citations_valid,
                missing_citations=self.critique_missing_cites,
                unsupported_claims=self.critique_unsupported,
                fix=self.critique_fix,
            )
        raise AssertionError(f"Unexpected schema requested: {name}")

    def complete(self, prompt, **kwargs):
        self.calls.append("complete")
        text = self.draft_answers.pop(0) if self.draft_answers else "Default answer grounded in [1]."

        class Result:
            thinking = ""
            model = "test-model"
            stop_reason = "end_turn"
            refusal_category = None
            input_tokens = 120
            output_tokens = 45
            refused = False

        res = Result()
        res.text = text
        return res


class Task48FakeRetriever:
    def __init__(self, chunks=None):
        self.chunks = (
            [_chunk("1", "k8s/pod.md", "Container lifecycle and exit codes")]
            if chunks is None
            else list(chunks)
        )
        self.retrieve_calls = 0

    def retrieve(self, question, **kwargs):
        self.retrieve_calls += 1
        return RetrievalResult(chunks=self.chunks, query_used=question, reranked=True)

    def retrieve_multi(self, queries, **kwargs):
        self.retrieve_calls += 1
        return self.chunks

    def rewrite_query(self, question, n=3):
        return [f"rewritten: {question}"]


class Task48PassthroughGuardrails:
    def check_input(self, question, use_llm=True):
        from advanced_rag.guardrails.pipeline import GuardrailResult

        return GuardrailResult(
            text=question, outcomes=[GuardrailOutcome(layer="shape", passed=True)]
        )

    def check_output(self, answer, question="", context="", use_llm=True):
        from advanced_rag.guardrails.pipeline import GuardrailResult

        return GuardrailResult(
            text=answer, outcomes=[GuardrailOutcome(layer="secret_egress", passed=True)]
        )


class Task48NoCache:
    def lookup(self, question):
        return None, "none"

    def store(self, question, payload):
        self.last_stored = (question, payload)


def task48_settings(**overrides) -> Settings:
    defaults = dict(
        enable_hyde=False,
        enable_crag=True,
        enable_self_rag=True,
        enable_text2sql=False,
        enable_guardrails=True,
        enable_cache=False,
        self_rag_max_retries=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def run(graph, question="why did my pod get OOMKilled?", thread="t-48"):
    return graph.invoke(
        initial_state(question), config={"configurable": {"thread_id": thread}}
    )


# =====================================================================
# Requirement 1: Relevant retrieved context
# =====================================================================


def test_crag_relevant_context(monkeypatch):
    llm = Task48FakeLLM(verdict="correct", verdict_reason="complete runbook matched")
    chunks = [_chunk("c1", "oomkilled.md", "Details on OOM")]
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    patch = grade_node({"chunks": chunks, "original_question": "why oom", "context": "[1] oomkilled.md"})
    assert patch["verdict"] is Verdict.CORRECT
    assert "complete runbook matched" in patch["verdict_reason"]


# =====================================================================
# Requirement 2: Partially relevant context
# =====================================================================


def test_crag_partially_relevant_context(monkeypatch):
    llm = Task48FakeLLM(verdict="ambiguous", verdict_reason="mentions pod but lacks exit code 137")
    chunks = [_chunk("c1", "pod-lifecycle.md", "General lifecycle")]
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    patch = grade_node({"chunks": chunks, "original_question": "exit code 137", "context": "[1] pod-lifecycle.md"})
    assert patch["verdict"] is Verdict.AMBIGUOUS
    assert "lacks exit code 137" in patch["verdict_reason"]


# =====================================================================
# Requirement 3: Irrelevant retrieved context
# =====================================================================


def test_crag_irrelevant_retrieved_context(monkeypatch):
    llm = Task48FakeLLM(verdict="incorrect", verdict_reason="off-topic nginx guide")
    chunks = [_chunk("c1", "nginx.md", "Nginx web server setup")]
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    patch = grade_node({"chunks": chunks, "original_question": "ceph rbd storage failure", "context": "[1] nginx.md"})
    assert patch["verdict"] is Verdict.INCORRECT


# =====================================================================
# Requirement 4: Malformed CRAG grading response
# =====================================================================


def test_crag_malformed_grading_response():
    # Test normalization of various LLM formatting variations
    g1 = ContextGrade(verdict="CORRECT.")
    assert g1.verdict == "correct"

    g2 = ContextGrade(verdict="partially relevant / ambiguous facts")
    assert g2.verdict == "ambiguous"

    g3 = ContextGrade(verdict="irrelevant context, off topic")
    assert g3.verdict == "incorrect"

    g4 = ContextGrade(verdict=None)
    assert g4.verdict == "ambiguous"


# =====================================================================
# Requirement 5: CRAG provider failure
# =====================================================================


def test_crag_provider_failure(monkeypatch):
    class CrashingLLM:
        def complete_json(self, *args, **kwargs):
            raise RuntimeError("503 Service Unavailable: Provider down")

    chunks = [_chunk("c1", "oom.md", "Container was killed due to memory limit")]
    monkeypatch.setattr(nodes, "get_llm", lambda: CrashingLLM())
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    # With keyword overlap in question ("killed"), fallback sets AMBIGUOUS rather than crashing
    patch = grade_node({
        "chunks": chunks,
        "original_question": "why was container killed?",
        "context": "[1] Container was killed due to memory limit",
    })
    assert patch["verdict"] is Verdict.AMBIGUOUS
    assert "grader unavailable" in patch["verdict_reason"]


# =====================================================================
# Requirement 6: Grounded answer
# =====================================================================


def test_self_rag_grounded_answer(monkeypatch):
    chunks = [_chunk("1", "k8s/pod.md", "Exit code 137 indicates OOMKilled.")]
    llm = Task48FakeLLM(
        critique_grounded=True,
        critique_cited=True,
        critique_addresses=True,
        critique_citations_valid=True,
        critique_missing_cites=False,
    )
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "what is exit code 137?",
        "context": "[1] Exit code 137 indicates OOMKilled.",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Exit code 137 means the container was OOMKilled [1].",
        "extractive": False,
    }
    patch = nodes.critique_node(state)
    assert patch["critique"] == ""
    assert patch["grounded"] is True
    assert patch["unsupported_claims"] == []


# =====================================================================
# Requirement 7: Partially grounded answer
# =====================================================================


def test_self_rag_partially_grounded_answer(monkeypatch):
    chunks = [_chunk("1", "k8s/pod.md", "Exit code 137 indicates OOMKilled.")]
    llm = Task48FakeLLM(
        critique_grounded=False,
        critique_unsupported=["kernel memory limit was set to 42GB"],
        critique_fix="Remove claim about 42GB kernel limit.",
    )
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "what is exit code 137?",
        "context": "[1] Exit code 137 indicates OOMKilled.",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Exit code 137 is OOMKilled [1]. The kernel memory limit was set to 42GB.",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert "Remove claim about 42GB" in patch["critique"]
    assert patch["grounded"] is False
    assert "kernel memory limit was set to 42GB" in patch["unsupported_claims"]
    assert patch["self_rag_attempts"] == 1


# =====================================================================
# Requirement 8: Unsupported answer claims
# =====================================================================


def test_self_rag_unsupported_answer_claims():
    critique = Critique(
        grounded=False,
        unsupported_claims=["Cluster is running Kubernetes 1.99", "Node has 512 GPUs"],
        fix="Drop ungrounded version and GPU specs.",
    )
    assert not critique.acceptable
    assert len(critique.unsupported_claims) == 2


# =====================================================================
# Requirement 9: Missing citation
# =====================================================================


def test_self_rag_missing_citation():
    chunks = [_chunk("1", "k8s/pod.md")]
    is_valid, cited, invalid, reason = validate_citations(
        "Exit code 137 means OOMKilled with no citations.",
        chunks,
    )
    assert is_valid is False
    assert cited == []
    assert "missing" in reason


# =====================================================================
# Requirement 10: Fabricated citation identifier
# =====================================================================


def test_self_rag_fabricated_citation_identifier():
    chunks = [_chunk("1", "k8s/pod.md"), _chunk("2", "k8s/deploy.md")]
    # Answer cites [99] which does not exist in the 2-chunk context
    is_valid, cited, invalid, reason = validate_citations(
        "This claim cites [1] and nonexistent source [99].",
        chunks,
    )
    assert is_valid is False
    assert 99 in invalid
    assert "fabricated citation identifiers [99]" in reason


# =====================================================================
# Requirement 11: Citation-to-context mapping
# =====================================================================


def test_citation_to_context_mapping():
    chunks = [
        _chunk("c1", "k8s/pod.md", text="pod info", section="Lifecycle"),
        _chunk("c2", "sre/oom.md", text="oom info", section="Triage"),
    ]
    state = {
        "context_chunks": chunks,
        "answer": "Pod lifecycle info is in [1].",
        "extractive": False,
    }
    citations = citations_from(state)
    assert len(citations) == 2

    # Chunk 1 was cited
    assert citations[0].citation_id == 1
    assert citations[0].source == "k8s/pod.md"
    assert citations[0].section == "Lifecycle"
    assert citations[0].referenced is True
    assert citations[0].document_id == "doc-c1"

    # Chunk 2 was supplied in context but not cited in text
    assert citations[1].citation_id == 2
    assert citations[1].source == "sre/oom.md"
    assert citations[1].referenced is False


# =====================================================================
# Requirement 12: Successful answer regeneration
# =====================================================================


def test_successful_answer_regeneration(monkeypatch):
    from advanced_rag.graph import nodes

    # Draft 1 is ungrounded; draft 2 is grounded
    llm = Task48FakeLLM(
        draft_answers=[
            "Ungrounded draft [1] with extra hallucination.",
            "Regenerated answer strictly grounded in [1].",
        ],
        critique_grounded=False,  # First critique fails
        critique_fix="Drop the extra hallucination.",
    )

    chunks = [_chunk("c1", "oomkilled.md", "Container was killed by OOM.")]
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings(self_rag_max_retries=1))

    # We make critique pass on the second attempt
    original_complete_json = llm.complete_json

    def alternating_complete_json(prompt, schema_model, **kwargs):
        if schema_model.__name__ == "Critique":
            if "Regenerated answer" in prompt:
                return schema_model(
                    grounded=True,
                    addresses_question=True,
                    cited=True,
                    citations_valid=True,
                    missing_citations=False,
                    unsupported_claims=[],
                    fix="none",
                )
        return original_complete_json(prompt, schema_model, **kwargs)

    llm.complete_json = alternating_complete_json

    graph = build_graph(task48_settings(self_rag_max_retries=1))
    result = run(graph, "why oom?")

    generates = [s for s in result["trace"] if s.node == "generate"]
    assert len(generates) == 2, "Expected exactly 1 retry (2 generate passes total)"
    assert "Regenerated answer" in result["answer"]
    assert result["grounded"] is True


# =====================================================================
# Requirement 13: Failed regeneration falls back to conservative answer
# =====================================================================


def test_failed_regeneration_falls_back_to_conservative(monkeypatch):
    from advanced_rag.graph import nodes

    # Both drafts fail critique
    llm = Task48FakeLLM(
        draft_answers=[
            "Draft 1 with hallucination [1].",
            "Draft 2 still has hallucination [1].",
        ],
        critique_grounded=False,
        critique_fix="Still ungrounded.",
    )
    chunks = [_chunk("c1", "oomkilled.md", "Verified container killed by OOM.")]
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings(self_rag_max_retries=1))

    graph = build_graph(task48_settings(self_rag_max_retries=1))
    result = run(graph, "why oom?")

    assert result["grounded"] is False
    assert result["extractive"] is True
    assert "could not verify the groundedness" in result["answer"]
    assert "Verified container killed by OOM." in result["answer"]


# =====================================================================
# Requirement 14: Maximum one regeneration attempt
# =====================================================================


def test_maximum_one_regeneration_attempt(monkeypatch):
    from advanced_rag.graph import nodes

    llm = Task48FakeLLM(
        critique_grounded=False,
        critique_fix="Never satisfied.",
    )
    chunks = [_chunk("c1", "oomkilled.md")]
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings(self_rag_max_retries=1))

    graph = build_graph(task48_settings(self_rag_max_retries=1))
    result = run(graph, "why oom?")

    generates = [s for s in result["trace"] if s.node == "generate"]
    assert len(generates) == 2, "Must never exceed 1 regeneration retry"


# =====================================================================
# Requirement 15: Disabled Self-RAG behavior
# =====================================================================


def test_disabled_self_rag_behavior(monkeypatch):
    from advanced_rag.graph import nodes

    llm = Task48FakeLLM()
    retriever = Task48FakeRetriever()
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings(enable_self_rag=False))

    graph = build_graph(task48_settings(enable_self_rag=False))
    node_names = set(graph.get_graph().nodes)
    assert "self_critique" not in node_names

    result = run(graph, "why oom?")
    trace_nodes = [s.node for s in result["trace"]]
    assert "self_critique" not in trace_nodes


# =====================================================================
# Requirement 16: Disabled CRAG behavior
# =====================================================================


def test_disabled_crag_behavior(monkeypatch):
    from advanced_rag.graph import nodes

    llm = Task48FakeLLM()
    retriever = Task48FakeRetriever()
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings(enable_crag=False))

    graph = build_graph(task48_settings(enable_crag=False))
    node_names = set(graph.get_graph().nodes)
    assert "grade_context" not in node_names
    assert "rewrite_query" not in node_names

    result = run(graph, "why oom?")
    trace_nodes = [s.node for s in result["trace"]]
    assert "grade_context" not in trace_nodes
    assert "rewrite_query" not in trace_nodes


# =====================================================================
# Requirement 17: Graph termination and fallback behavior
# =====================================================================


def test_graph_termination_on_empty_retrieval(monkeypatch):
    from advanced_rag.graph import nodes

    llm = Task48FakeLLM(verdict="incorrect")
    retriever = Task48FakeRetriever(chunks=[])  # Empty retrieval
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    graph = build_graph(task48_settings())
    result = run(graph, "unknown term")

    assert result["verdict"] is Verdict.INCORRECT
    assert "could not find any relevant documentation" in result["answer"]
    assert result["trace"][-1].node == "finalize"


# =====================================================================
# Requirement 18: API response compatibility
# =====================================================================


def test_api_response_compatibility(monkeypatch):
    from advanced_rag.api import main

    def fake_ask(question, **kwargs):
        return AnswerResponse(
            question=question,
            answer="Detailed answer grounded in [1].",
            route=Route.VECTOR,
            citations=[
                Citation(
                    source="k8s/pod.md",
                    title="Pod Spec",
                    section="Resources",
                    score=0.9123,
                    citation_id=1,
                    document_id="doc-123",
                    doc_type="runbook",
                    referenced=True,
                )
            ],
            thread_id="t-48",
            cached=False,
            extractive=False,
            latency_ms=150,
        )

    monkeypatch.setattr(main.pipeline, "ask", fake_ask)
    monkeypatch.setattr(main.pipeline, "get_graph", lambda: None)

    with TestClient(main.app) as client:
        resp = client.post("/ask", json={"question": "how to configure memory limits?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["question"] == "how to configure memory limits?"
        assert data["answer"] == "Detailed answer grounded in [1]."
        assert data["route"] == "vector"
        assert len(data["citations"]) == 1
        cit = data["citations"][0]
        assert cit["source"] == "k8s/pod.md"
        assert cit["citation_id"] == 1
        assert cit["document_id"] == "doc-123"
        assert cit["referenced"] is True
        assert cit["score"] == 0.9123


# =====================================================================
# Part 6: Focused Critique Failure & Fallback Tests
# =====================================================================


class FailingCritiqueLLM(Task48FakeLLM):
    def complete_json(self, prompt, schema_model, **kwargs):
        name = schema_model.__name__
        self.calls.append(name)
        if name == "Critique":
            raise RuntimeError("503 Service Unavailable: Critique provider failed")
        return super().complete_json(prompt, schema_model, **kwargs)


def test_critique_provider_failure_valid_citations(monkeypatch):
    """Critique failure with valid citations falls back to extractive answer."""
    chunks = [_chunk("c1", "k8s/pod.md", "Container exit code 137 details")]
    llm = FailingCritiqueLLM(draft_answers=["Exit code 137 indicates OOMKilled [1]."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "what is exit code 137?",
        "context": "[1] Container exit code 137 details",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Exit code 137 indicates OOMKilled [1].",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert patch["extractive"] is True
    assert patch["grounded"] is None
    assert patch["critique"] == ""
    assert "could not verify the groundedness" in patch["answer"]
    assert "Container exit code 137 details" in patch["answer"]


def test_critique_provider_failure_fabricated_citations(monkeypatch):
    """Critique failure with fabricated citations discards [99] and uses extractive context."""
    chunks = [_chunk("c1", "k8s/pod.md", "Real chunk text")]
    llm = FailingCritiqueLLM(draft_answers=["Fabricated claim with bad index [99]."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "question?",
        "context": "[1] Real chunk text",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Fabricated claim with bad index [99].",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert "[99]" not in patch["answer"]
    assert patch["extractive"] is True
    assert patch["grounded"] is None
    assert "Real chunk text" in patch["answer"]


def test_critique_provider_failure_missing_citations(monkeypatch):
    """Critique failure with missing citations discards draft and falls back to extractive."""
    chunks = [_chunk("c1", "k8s/pod.md", "Real chunk text")]
    llm = FailingCritiqueLLM(draft_answers=["Answer with zero citations whatsoever."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "question?",
        "context": "[1] Real chunk text",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Answer with zero citations whatsoever.",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert patch["extractive"] is True
    assert patch["grounded"] is None
    assert "could not verify the groundedness" in patch["answer"]


def test_critique_provider_failure_empty_context(monkeypatch):
    """Critique failure when context is empty returns honest insufficient-evidence response."""
    llm = FailingCritiqueLLM(draft_answers=["Draft on empty context."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "question?",
        "context": "",
        "context_chunks": [],
        "chunks": [],
        "answer": "Draft on empty context.",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert patch["abstained"] is True
    assert patch["grounded"] is None
    assert patch["extractive"] is False
    assert "could not find any relevant documentation" in patch["answer"]


def test_critique_provider_failure_discards_original_synthesized_draft(monkeypatch):
    """Ensures the original draft string is completely discarded."""
    chunks = [_chunk("c1", "k8s/pod.md", "Documentation text")]
    llm = FailingCritiqueLLM(draft_answers=["Unique draft text that must be purged."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "question?",
        "context": "[1] Documentation text",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Unique draft text that must be purged.",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert "Unique draft text that must be purged." not in patch["answer"]


def test_critique_provider_failure_does_not_trigger_another_llm_call(monkeypatch):
    """Critique failure stops immediately without secondary recovery LLM calls."""
    chunks = [_chunk("c1", "k8s/pod.md", "Documentation text")]
    llm = FailingCritiqueLLM(draft_answers=["Draft [1]."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "question?",
        "context": "[1] Documentation text",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Draft [1].",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    initial_calls = len(llm.calls)
    nodes.critique_node(state)
    assert len(llm.calls) == initial_calls + 1, "Only the failed critique call must have been attempted"


def test_critique_provider_failure_preserves_grounded_none(monkeypatch):
    """Groundedness is explicitly unverified (None), not True or False."""
    chunks = [_chunk("c1", "k8s/pod.md", "Documentation text")]
    llm = FailingCritiqueLLM(draft_answers=["Draft [1]."])
    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    state = {
        "original_question": "question?",
        "context": "[1] Documentation text",
        "context_chunks": chunks,
        "chunks": chunks,
        "answer": "Draft [1].",
        "extractive": False,
        "self_rag_attempts": 0,
    }
    patch = nodes.critique_node(state)
    assert patch.get("grounded") is None


def test_fallback_uses_selected_context_chunks(monkeypatch):
    """Fallback uses context_chunks and ignores candidates outside context_chunks."""
    selected = [_chunk("c1", "k8s/pod.md", "Selected chunk text")]
    all_chunks = selected + [_chunk("c2", "k8s/unrelated.md", "Unrelated text")]

    llm = FailingCritiqueLLM(draft_answers=["Draft [1]."])
    retriever = Task48FakeRetriever(all_chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    def fake_select(chunks, **kwargs):
        return [chunks[0]]

    monkeypatch.setattr(nodes, "select_context_chunks", fake_select)

    graph = build_graph(task48_settings())
    result = run(graph, "test question")

    assert "Selected chunk text" in result["answer"]
    assert "Unrelated text" not in result["answer"]
    cits = citations_from(result)
    assert len(cits) == 1
    assert cits[0].source == "k8s/pod.md"


def test_fallback_preserves_source_paths_and_document_ids():
    """Fallback citations preserve source paths, section, doc_type, and document_id."""
    chunks = [
        _chunk("c1", "k8s/pod.md", "Pod text", section="Lifecycle"),
        _chunk("c2", "k8s/node.md", "Node text", section="Triage"),
    ]
    state = {
        "context_chunks": chunks,
        "answer": "**[1] k8s/pod.md › Lifecycle**\n\n> Pod text\n\n**[2] k8s/node.md › Triage**\n\n> Node text",
        "extractive": True,
    }
    cits = citations_from(state)
    assert len(cits) == 2
    assert cits[0].citation_id == 1
    assert cits[0].source == "k8s/pod.md"
    assert cits[0].section == "Lifecycle"
    assert cits[0].document_id == "doc-c1"
    assert cits[0].doc_type == "runbook"
    assert cits[0].referenced is True

    assert cits[1].citation_id == 2
    assert cits[1].source == "k8s/node.md"
    assert cits[1].section == "Triage"
    assert cits[1].document_id == "doc-c2"
    assert cits[1].referenced is True


def test_fallback_citation_ids_align_with_answer_markers():
    """Answer [1], [2] markers match citation_id 1, 2."""
    chunks = [_chunk("c1", "k8s/pod.md"), _chunk("c2", "k8s/node.md")]
    state = {
        "context_chunks": chunks,
        "answer": "**[1] k8s/pod.md**\n\n> text\n\n**[2] k8s/node.md**\n\n> text",
        "extractive": True,
    }
    cits = citations_from(state)
    assert [c.citation_id for c in cits] == [1, 2]
    assert all(c.referenced for c in cits)


def test_fallback_correctly_marks_referenced_citations():
    """Uncited context chunks in answer are marked referenced=False."""
    chunks = [_chunk("c1", "k8s/pod.md"), _chunk("c2", "k8s/node.md")]
    state = {
        "context_chunks": chunks,
        "answer": "Only chunk [1] is cited in this text.",
        "extractive": False,
    }
    cits = citations_from(state)
    assert cits[0].referenced is True
    assert cits[1].referenced is False


def test_weak_crag_retrieval_includes_appropriate_warning():
    """When CRAG verdict is INCORRECT, extractive answer includes weak match warning."""
    from advanced_rag.llm.extractive import extractive_answer
    chunks = [_chunk("c1", "k8s/pod.md", "Irrelevant text")]
    ans = extractive_answer(chunks, verdict=Verdict.INCORRECT)
    assert "weak match" in ans.lower()


def test_empty_evidence_returns_insufficient_evidence_response():
    """Empty chunks return honest insufficient evidence."""
    from advanced_rag.llm.extractive import extractive_answer
    ans = extractive_answer([])
    assert "retrieval returned no passages" in ans


def test_failed_regeneration_and_critique_failure_both_terminate_safely(monkeypatch):
    """Verifies that both failed regeneration and critique failure terminate at finalize."""
    chunks = [_chunk("c1", "k8s/pod.md", "Container lifecycle")]

    # 1. Critique provider failure path
    llm1 = FailingCritiqueLLM(draft_answers=["Draft [1]."])
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm1)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    graph = build_graph(task48_settings())
    res1 = run(graph, "test q")
    assert res1["trace"][-1].node == "finalize"
    assert res1["extractive"] is True

    # 2. Failed regeneration budget exhausted path
    llm2 = Task48FakeLLM(
        draft_answers=["Draft 1 [1].", "Draft 2 [1]."],
        critique_grounded=False,
        critique_fix="Keep failing.",
    )
    monkeypatch.setattr(nodes, "get_llm", lambda: llm2)
    res2 = run(graph, "test q 2")
    assert res2["trace"][-1].node == "finalize"
    assert res2["extractive"] is True


def test_no_infinite_graph_loop(monkeypatch):
    """Ensures graph terminates and never loops infinitely even with consecutive critique failures."""
    chunks = [_chunk("c1", "k8s/pod.md")]
    llm = FailingCritiqueLLM(draft_answers=["Draft [1]."])
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    graph = build_graph(task48_settings())
    res = run(graph, "loop test")
    assert res["trace"][-1].node == "finalize"
    assert res["extractive"] is True


def test_generation_failed_behavior_remains_unchanged(monkeypatch):
    """When generation fails with LLM outage, critique is skipped."""
    class FailingGenLLM(Task48FakeLLM):
        def complete(self, *args, **kwargs):
            raise RuntimeError("LLM inference unavailable")

    chunks = [_chunk("c1", "k8s/pod.md")]
    llm = FailingGenLLM()
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    graph = build_graph(task48_settings())
    result = run(graph, "test q")
    assert result["generation_failed"] is True
    assert "language model is unavailable" in result["answer"]
    critique_steps = [s for s in result["trace"] if s.node == "self_critique"]
    assert len(critique_steps) == 1
    assert "skipped" in critique_steps[0].detail


def test_blocked_behavior_remains_unchanged(monkeypatch):
    """When generation is refused/blocked, critique is skipped."""
    class RefusingLLM(Task48FakeLLM):
        def complete(self, *args, **kwargs):
            class RefusedResult:
                refused = True
                refusal_category = "safety_policy"
                text = "Refused"
                input_tokens = 10
                output_tokens = 0
            return RefusedResult()

    chunks = [_chunk("c1", "k8s/pod.md")]
    llm = RefusingLLM()
    retriever = Task48FakeRetriever(chunks)
    cache = Task48NoCache()

    monkeypatch.setattr(nodes, "get_llm", lambda: llm)
    monkeypatch.setattr(nodes, "get_retriever", lambda: retriever)
    monkeypatch.setattr(nodes, "get_guardrails", lambda: Task48PassthroughGuardrails())
    monkeypatch.setattr(nodes, "get_cache", lambda: cache)
    monkeypatch.setattr(nodes, "get_settings", lambda: task48_settings())

    graph = build_graph(task48_settings())
    result = run(graph, "test q")
    assert result["blocked"] is True
    assert "can't answer that request" in result["answer"].lower()
    critique_steps = [s for s in result["trace"] if s.node == "self_critique"]
    assert len(critique_steps) == 1
    assert "skipped" in critique_steps[0].detail


def test_deterministic_guardrails_still_execute():
    """Inbound shape/length guardrails still execute deterministically."""
    from advanced_rag.guardrails.pipeline import get_guardrails
    guardrails = get_guardrails()
    res = guardrails.check_input("a" * 5000)
    assert res.blocked is True
    assert any(o.layer == "shape" and not o.passed for o in res.outcomes)
