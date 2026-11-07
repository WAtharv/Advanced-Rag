
"""User Question
     ↓
Input Guardrails
     ↓
Cache Check
     ↓
Routing (Vector / SQL / Both)
     ↓
Retrieve Documents
     ↓
Grade Context (CRAG)
     ↓
Rewrite Query (if needed)
     ↓
Generate Answer
     ↓
Self Critique (Self-RAG)
     ↓
SQL Approval & Execute (if SQL)
     ↓
Output Guardrails
     ↓
Cache Result
     ↓
Final Answer


Self-RAG (Self-Reflective Retrieval-Augmented Generation) is an advanced AI framework where a large language model (LLM) 
dynamically decides when to fetch external data and critically evaluates its own text using special "reflection tokens".

## Graph nodes.

Each node is a plain function of state -> state patch, with no knowledge of the
edges around it. That keeps every stage testable in isolation and lets the
builder rewire the pipeline from feature flags without touching this file."""

from __future__ import annotations

import logging

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from advanced_rag.cache import get_cache
from advanced_rag.config import get_settings
from advanced_rag.graph.state import RagState
from advanced_rag.guardrails import get_guardrails
from advanced_rag.llm import prompts
from advanced_rag.llm.client import get_llm, llm_available
from advanced_rag.llm.extractive import extractive_answer
from advanced_rag.models import Citation, Route, TraceStep, Verdict
from advanced_rag.observability import timed
from advanced_rag.retrieval import format_context, get_retriever
from advanced_rag.text2sql import executor
from advanced_rag.text2sql.generator import get_sql_generator

logger = logging.getLogger(__name__)


def _extractive_mode(state: RagState) -> bool:
    """True when this run must answer by quoting passages, not generating prose.

    That happens either because no LLM is configured at all, or because the
    caller explicitly asked for the no-LLM path (`force_extractive`). Every
    LLM-backed node consults this so the two look identical downstream.
    """
    return bool(state.get("force_extractive")) or not llm_available()


def _use_llm(state: RagState) -> bool:
    return not _extractive_mode(state)


# --------------------------------------------------------------------- schemas


class RouteDecision(BaseModel):
    route: str = Field(description="one of: vector, sql, both, reject")
    reason: str = Field(description="one sentence")


class ContextGrade(BaseModel):
    verdict: str = Field(description="one of: correct, ambiguous, incorrect")
    reason: str = Field(description="one sentence naming what is present or missing")


class Critique(BaseModel):
    grounded: bool = Field(description="every claim is supported by the context")
    addresses_question: bool = Field(description="the answer resolves what was asked")
    cited: bool = Field(description="claims carry [n] citations")
    fix: str = Field(description="what to change, or 'none'")

    @property
    def acceptable(self) -> bool:
        return self.grounded and self.addresses_question and self.cited


# ----------------------------------------------------------------------- nodes


def guardrail_input_node(state: RagState) -> dict:                      # Validate user input before doing anything expensive.
                                                                        # Checks: Harmful content, Prompt injection, Unsupported requests
    """Layers 1-6: everything that runs before a retrieval token is spent."""
    trace: list = []
    with timed(trace, "guardrail_input") as step:
        result = get_guardrails().check_input(state["question"])
        step.detail = f"{len(result.outcomes)} layers, blocked={result.blocked}"
    return {
        "question": result.text,
        "guardrails": result.outcomes,
        "blocked": result.blocked,
        "block_message": result.message,
        "answer": result.message if result.blocked else "",
        "trace": trace,
    }


def cache_lookup_node(state: RagState) -> dict:                         # Check whether the answer already exists.
                                                                        # Looks in: Exact cache, Semantic cache; if found pipeline can stop early
    """Exact then semantic cache. A hit short-circuits the whole pipeline."""
    trace: list = []
    with timed(trace, "cache_lookup") as step:
        payload, kind = get_cache().lookup(state["original_question"])
        # The exact and semantic tiers key on the question alone, so a hit can be
        # an answer produced in the other mode. Never serve it: a "Without LLM"
        # request must not receive a cached generated answer, nor the reverse.
        # The stored payload records which mode wrote it via `extractive`.
        if payload is not None and bool(payload.get("extractive")) != _extractive_mode(state):
            payload, kind = None, "none (other answer mode)"
        step.detail = kind
    if payload is None:
        return {"cached": False, "cache_kind": "none", "trace": trace}
    return {
        "cached": True,                                                 # if found cache=True, "answer": cached_answer
        "cache_kind": kind,
        "answer": payload.get("answer", ""),
        "context": payload.get("context", ""),
        "route": Route(payload.get("route", Route.VECTOR.value)),
        "citations": [Citation(**c) for c in payload.get("citations") or []],
        # Restored so a cache hit still declares itself extractive; the
        # stored body says so in prose, and the flag must not disagree.
        "extractive": bool(payload.get("extractive")),
        "trace": trace,
    }


def route_node(state: RagState) -> dict:                                # Decide where data should come from.
                                                                        # Possible routes: VECTOR, SQL, BOTH, REJECT 
                                                                        # Uses LLM router: RouteDecision()

    """Decide whether the question needs documents, the database, or both."""
    settings = get_settings()
    trace: list = []
    with timed(trace, "route") as step:
        if not settings.enable_text2sql:
            step.detail = "text2sql disabled - forcing vector"
            return {"route": Route.VECTOR, "trace": trace}
        if not _use_llm(state):
            # Classifying a question needs a model. Vector retrieval is the safe
            # default and, unlike SQL, needs no credentials to run. This also
            # covers the "Without LLM" mode, where SQL routing is unavailable.
            reason = "offline mode" if not llm_available() else "no-LLM mode requested"
            step.detail = f"{reason} - vector retrieval only"
            return {"route": Route.VECTOR, "trace": trace}
        try:
            decision = get_llm().complete_json(
                state["question"], RouteDecision, system=prompts.ROUTER_SYSTEM
            )
            route = Route(decision.route)
            step.detail = f"{route.value}: {decision.reason}"
        except Exception:
            # Vector retrieval is the safe default: it cannot touch the database.
            logger.exception("Routing failed - defaulting to vector retrieval")
            route = Route.VECTOR
            step.detail = "router unavailable - defaulted to vector"

    patch: dict = {"route": route, "trace": trace}
    if route is Route.REJECT:
        patch |= {
            "blocked": True,
            "block_message": "That question is outside what this assistant covers.",
            "answer": "That question is outside what this assistant covers.",
        }
    return patch


def retrieve_node(state: RagState) -> dict:                             # Fetch relevant documents.
    """HyDE -> hybrid search -> cross-encoder rerank."""                # <- Uses 
    """
    Query
        ↓
        HyDE Query
        ↓
        Vector Search
        ↓
        BM25 Search
        ↓
        Merge Results
        ↓
    Cross Encoder Rerank    
    """
    
    settings = get_settings()
    retriever = get_retriever()
    trace: list = []
    attempts = state.get("retrieval_attempts", 0) + 1

    with timed(trace, "retrieve") as step:
        rewrites = state.get("rewrites") or []
        if rewrites:
            # Corrective pass: search the rewritten queries, not the original.
            chunks = retriever.retrieve_multi(rewrites, question=state["original_question"])
            hyde_doc = state.get("hyde_document")
            step.detail = f"{len(chunks)} chunks from {len(rewrites)} rewritten queries"
        else:
            result = retriever.retrieve(state["question"], use_hyde=settings.enable_hyde)
            chunks, hyde_doc = result.chunks, result.hyde_document
            step.detail = (
                f"{len(chunks)} chunks, hyde={'yes' if hyde_doc else 'no'}, "
                f"top={result.top_score:.3f}"
            )

    return {                                                            # Output: chunks, context, hyde_document
        "chunks": chunks,
        "context": format_context(chunks),
        "hyde_document": hyde_doc,
        "retrieval_attempts": attempts,
        "trace": trace,
    }


def grade_node(state: RagState) -> dict:                                # CRAG validation: CORRECT, AMBIGUOUS, INCORRECT
    """CRAG: judge the retrieved context before spending a generation on it."""
    settings = get_settings()
    trace: list = []
    chunks = state.get("chunks") or []

    with timed(trace, "grade_context") as step:
        if not chunks:
            step.detail = "no chunks retrieved"
            return {
                "verdict": Verdict.INCORRECT,
                "verdict_reason": "retrieval returned nothing",
                "trace": trace,
            }

        # Cheap signal first: if a cross-encoder is unconvinced, there is no point
        # paying for a grader call. Only a cross-encoder score is trusted for this.
        # Measured with the lexical fallback: four of four semantically-matched
        # queries scored 0.005-0.133 while retrieval had returned exactly the right
        # document, so gating on it rejected good context every time.
        authoritative = [c for c in chunks if c.rerank_is_authoritative]
        if authoritative:
            top = max(c.score for c in authoritative)
            if top < settings.crag_relevance_floor:
                step.detail = f"top cross-encoder score {top:.3f} below floor"
                return {
                    "verdict": Verdict.INCORRECT,
                    "verdict_reason": f"best passage scored {top:.2f}, below the relevance floor",
                    "trace": trace,
                }

        if not _use_llm(state):
            # The cross-encoder floor above is the only grading available
            # without a model, and it has already had its say. This also covers
            # the "Without LLM" mode.
            step.detail = "no LLM - context not graded"
            return {
                "verdict": Verdict.CORRECT,
                "verdict_reason": "no LLM configured; context accepted unjudged",
                "trace": trace,
            }

        prompt = (
            "Question: " + state["original_question"] + "\n\nRetrieved context:\n"
            + state.get("context", "")
        )
        try:
            grade = get_llm().complete_json(prompt, ContextGrade, system=prompts.CRAG_GRADER_SYSTEM)
            verdict = Verdict(grade.verdict)
            reason = grade.reason
        except Exception:
            logger.exception("Context grading failed - treating context as usable")
            verdict, reason = Verdict.CORRECT, "grader unavailable"
        step.detail = f"{verdict.value}: {reason}"

    return {"verdict": verdict, "verdict_reason": reason, "trace": trace}


def rewrite_node(state: RagState) -> dict:                         # Correct bad retrieval.
    """CRAG's corrective step: propose better queries after a weak retrieval."""
    trace: list = []
    with timed(trace, "rewrite_query") as step:
        rewrites = get_retriever().rewrite_query(state["original_question"])
        step.detail = f"{len(rewrites)} rewrites: " + "; ".join(rewrites[:3])
    return {"rewrites": rewrites, "trace": trace}


def generate_node(state: RagState) -> dict:                        # Create final answer.
    """Compose the answer from document context and any SQL results."""
    trace: list = []
    settings = get_settings()

    context_parts = []
    if state.get("context"):
        context_parts.append(state["context"])
    if state.get("sql_rows_text"):
        sql = state.get("sql")
        context_parts.append(
            "[SQL] Query executed against the operations database:\n"
            + (sql.sql if sql else "")
            + "\n\nResult:\n"
            + state["sql_rows_text"]
        )
    context = "\n\n".join(context_parts) or "(no context available)"

    instructions = ""
    if state.get("verdict") is Verdict.INCORRECT:
        instructions = (
            "\n\nThe retrieved context was judged insufficient for this question. "
            "Say clearly what is missing instead of filling the gap, and suggest "
            "where the engineer should look next."
        )
    if state.get("critique"):
        # Self-RAG retry: the critique is the only new information this pass has.
        instructions += "\n\nA reviewer rejected your previous draft: " + state["critique"]

    prompt = (
        "Question: " + state["original_question"] + "\n\nContext:\n" + context + instructions
    )
    with timed(trace, "generate") as step:
        if not _use_llm(state):
            # Retrieval already did the useful half of the job. Quote it. Reached
            # with no model configured, or in the UI's "Without LLM" mode.
            answer = extractive_answer(
                state.get("chunks") or [],
                verdict=state.get("verdict"),
                sql=(state["sql"].sql if state.get("sql") else ""),
                sql_rows_text=state.get("sql_rows_text") or "",
            )
            step.detail = f"extractive - {len(answer)} chars, no LLM"
            # Deliberately not `generation_failed`: nothing failed, and that flag
            # suppresses caching. An extractive answer is a pure function of the
            # retrieved chunks, so it is perfectly cacheable.
            return {"answer": answer, "extractive": True, "trace": trace}
        try:
            # Client construction is inside the try on purpose: credentials can
            # be present but invalid, and that should degrade into a readable
            # answer rather than a 500 from the API layer.
            result = get_llm().complete(
                prompt,
                system=prompts.ANSWER_SYSTEM,
                effort=settings.llm_effort,
            )
        except Exception as exc:
            logger.exception("Answer generation failed")
            step.detail = f"failed: {type(exc).__name__}"
            return {
                "answer": (
                    "I could not generate an answer because the language model is "
                    f"unavailable ({type(exc).__name__}). Retrieval did run - the "
                    "cited sources below are the passages that matched."
                ),
                "generation_failed": True,
                "trace": trace,
            }
        if result.refused:
            step.detail = f"refused ({result.refusal_category})"
            return {
                "answer": "I can't answer that request.",
                "blocked": True,
                "block_message": f"model refusal: {result.refusal_category}",
                "trace": trace,
            }
        step.detail = f"{len(result.text)} chars, {result.output_tokens} output tokens"

    return {
        "answer": result.text,
        "input_tokens": state.get("input_tokens", 0) + result.input_tokens,
        "output_tokens": state.get("output_tokens", 0) + result.output_tokens,
        "trace": trace,
    }


def critique_node(state: RagState) -> dict:                        # Self-RAG verification.
                                                                   # Grounded?
                                                                   # Answers Question?
                                                                   # Citations Present?
                                                                   # if failed critique.fix ; and regerate answer 
    """Self-RAG: grade the draft against its own context before returning it."""
    trace: list = []
    with timed(trace, "self_critique") as step:
        if not _use_llm(state):
            # Nothing to critique: an extractive answer is quoted source text,
            # so there is no paraphrase that could have drifted from it.
            reason = "offline mode" if not llm_available() else "no-LLM mode"
            step.detail = f"{reason} - extractive answer, nothing to critique"
            return {"critique": "", "trace": trace}

        prompt = (
            "Question: " + state["original_question"]
            + "\n\nContext:\n" + state.get("context", "(none)")
            + "\n\nDraft answer:\n" + state.get("answer", "")
        )
        try:
            critique = get_llm().complete_json(prompt, Critique, system=prompts.SELF_RAG_SYSTEM)
        except Exception:
            logger.exception("Self-critique failed - accepting the draft")
            step.detail = "critic unavailable"
            return {"critique": "", "trace": trace}

        step.detail = (
            f"grounded={critique.grounded} addresses={critique.addresses_question} "
            f"cited={critique.cited}"
        )
        if critique.acceptable:
            return {"critique": "", "trace": trace}
        return {
            "critique": critique.fix,
            "self_rag_attempts": state.get("self_rag_attempts", 0) + 1,
            "trace": trace,
        }


def sql_generate_node(state: RagState) -> dict:                    # Convert question to SQL.
    """Draft a read-only query. Nothing runs yet."""               # 
    trace: list = []
    with timed(trace, "sql_generate") as step:
        proposal = get_sql_generator().generate(state["question"])
        step.detail = proposal.error or f"proposed a query over {', '.join(proposal.tables)}"
    return {"sql": proposal, "trace": trace}


def sql_approval_node(state: RagState) -> dict:                     # Human-in-the-loop approval.
    """Pause the graph and hand the query to a human.               # Approve / Reject
                                                                    # Protects databases.
    `interrupt()` persists the state through the checkpointer and raises out of
    the run. Resuming with Command(resume={"approved": bool}) continues from
    exactly here, so no earlier work is repeated.
    """
    proposal = state.get("sql")
    if proposal is None or not proposal.sql:
        return {"awaiting_approval": False}

    decision = interrupt(                                           # interrupt 
        {
            "type": "sql_approval",
            "question": state["original_question"],
            "sql": proposal.sql,
            "rationale": proposal.rationale,
            "tables": proposal.tables,
        }
    )
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    updated = proposal.model_copy(update={"approved": approved})
    trace = [TraceStep(node="sql_approval", detail="approved" if approved else "rejected")]
    if approved:
        return {"sql": updated, "awaiting_approval": False, "trace": trace}
    return {
        "sql": updated,
        "awaiting_approval": False,
        "answer": "The query was not approved, so nothing was run against the database.",
        "trace": trace,
    }


def sql_execute_node(state: RagState) -> dict:                               #  Run approved SQL.
    """Run the approved query and render its rows for the answer prompt."""  # returns rows, columns and row_count 
    trace: list = []                                                         # Converts rows into text for LLM.
    proposal = state.get("sql")
    with timed(trace, "sql_execute") as step:
        if proposal is None or not proposal.approved or not proposal.sql:
            step.detail = "skipped - not approved"
            return {"trace": trace}
        try:
            result = executor.execute(proposal.sql)
        except Exception as exc:
            logger.exception("SQL execution failed")
            step.detail = f"failed: {exc}"
            return {
                "sql": proposal.model_copy(update={"error": str(exc)}),
                "sql_rows_text": "",
                "trace": trace,
            }
        rendered = executor.render_rows(result)
        step.detail = f"{result.row_count} rows"

    return {
        "sql": proposal.model_copy(
            update={
                "columns": result.columns,
                "rows": result.rows,
                "row_count": result.row_count,
            }
        ),
        "sql_rows_text": rendered,
        "trace": trace,
    }


def guardrail_output_node(state: RagState) -> dict:                        # Validate generated answer.
                                                                           # check:
                                                                            # Hallucinations
                                                                            # Unsafe output
                                                                            # Policy violations
                                                                            # Grounding 
                                                                
    """Layers 7-9, applied to the finished answer."""
    trace: list = []
    # A failed generation returns a fixed system notice ("the model is
    # unavailable..."), and a model refusal returns "I can't answer that" - neither
    # is model-authored prose. Feeding the former to output_review (layer 9) gets it
    # blocked for "claiming the model is unavailable despite context being
    # available", which turns a transient 503 into a hard "Request blocked" and
    # hides the retrieved sources. There is nothing to review, so pass the honest
    # fallback (and its citations) straight through.
    if state.get("generation_failed") or state.get("blocked"):
        with timed(trace, "guardrail_output") as step:
            step.detail = "skipped - no generated answer to review"
        return {"trace": trace}
    with timed(trace, "guardrail_output") as step:
        result = get_guardrails().check_output(
            state.get("answer", ""),
            question=state["original_question"],
            context=state.get("context", ""),
        )
        step.detail = f"{len(result.outcomes)} layers, blocked={result.blocked}"

    patch: dict = {"guardrails": result.outcomes, "trace": trace}
    if result.blocked:
        patch |= {"blocked": True, "block_message": result.message, "answer": result.message}
    else:
        patch["answer"] = result.text
    return patch


def finalize_node(state: RagState) -> dict:                                     # Store answer in cache.
    """Store the answer in the cache. Never cache a blocked or failed run."""   # 
    trace: list = []
    with timed(trace, "finalize") as step:
        if state.get("blocked") or state.get("cached") or not state.get("answer"):
            step.detail = "not cached"
            return {"trace": trace}
        if state.get("generation_failed"):
            # An outage must not be remembered as an answer; otherwise every
            # identical question is served the error for the whole cache TTL.
            step.detail = "not cached (generation failed)"
            return {"trace": trace}
        # SQL answers depend on live data, so caching them would serve stale
        # numbers under a question that looks identical.
        if state.get("route") in (Route.SQL, Route.BOTH):
            step.detail = "not cached (live SQL result)"
            return {"trace": trace}
        get_cache().store(
            state["original_question"],
            {
                "answer": state["answer"],
                "context": state.get("context", ""),
                "route": Route(state.get("route", Route.VECTOR)).value,
                "extractive": bool(state.get("extractive")),
                # Without these, a cache hit returns an answer whose [n] markers
                # point at nothing - the sources panel comes back empty.
                "citations": [c.model_dump() for c in citations_from(state)],
            },
        )
        step.detail = "cached"
    return {"trace": trace}


def citations_from(state: RagState) -> list[Citation]:                # Create source references.
    """Citations for the response.

    Prefers ones already in state - a cache hit restores them there without ever
    having retrieved chunks to derive them from.
    """
    restored = state.get("citations")
    if restored:
        return list(restored)
    return [
        Citation(
            source=hit.chunk.source,
            title=hit.chunk.title,
            section=hit.chunk.section,
            score=round(hit.score, 4),
        )
        for hit in state.get("chunks") or []
    ]


"""
Question → Guardrails → Cache → Route → Retrieve → Grade(CRAG) → Rewrite(if needed) → Generate → Self-Critique(Self-RAG) → SQL Approval/Execution(if needed) → Output Guardrails → Cache → Answer

✅ Guardrails
✅ Caching (Exact + Semantic)
✅ Hybrid Search
✅ HyDE
✅ Cross-Encoder Reranking
✅ CRAG (Context Grading + Rewrite)
✅ Self-RAG (Self Critique)            # when to retrieve external data, special reflective token 
✅ Text-to-SQL
✅ Human Approval for SQL
✅ Output Validation
✅ Citations
✅ Observability/Tracing
✅ Offline Extractive Mode (Without LLM)
"""