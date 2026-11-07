"""Public entry point: run the graph and shape the result for callers.

Also owns the human-in-the-loop protocol. `ask()` may return with
`awaiting_approval=True`, in which case the run is parked in the checkpointer
under `thread_id` and `resume()` continues it once a human decides.
"""

from __future__ import annotations

import logging
import time
import uuid             # Generate Random unique id's 
from typing import Any

from langgraph.types import Command

from advanced_rag.graph.builder import get_graph
from advanced_rag.graph.nodes import citations_from
from advanced_rag.graph.state import initial_state
from advanced_rag.models import AnswerResponse, Route, SqlProposal, TraceStep

logger = logging.getLogger(__name__)

#: LangGraph needs a recursion ceiling; the CRAG and Self-RAG loops are bounded
#: independently, so this only catches a genuine wiring mistake.
RECURSION_LIMIT = 40


def ask(
    question: str,
    *,
    thread_id: str | None = None,
    force_extractive: bool = False,
    graph=None,
) -> AnswerResponse:
    """Answer a question, pausing for approval if SQL needs to run.

    `force_extractive` selects the no-LLM path even when a model is configured:
    the answer is quoted runbook passages rather than generated prose. It is
    persisted in the run's state, so a resumed run keeps the mode it started in.
    """
    graph = graph or get_graph()
    thread_id = thread_id or uuid.uuid4().hex
    config = _config(thread_id)
    started = time.perf_counter()

    result = graph.invoke(
        initial_state(question, force_extractive=force_extractive), config=config
    )
    return _to_response(question, result, thread_id, started)


def resume(thread_id: str, *, approved: bool, graph=None) -> AnswerResponse:
    """Continue a run that stopped at the SQL approval gate."""
    graph = graph or get_graph()
    config = _config(thread_id)
    started = time.perf_counter()

    snapshot = graph.get_state(config)
    if not snapshot.values:
        raise KeyError(f"no run found for thread_id {thread_id!r}")
    question = snapshot.values.get("original_question", "")

    result = graph.invoke(Command(resume={"approved": approved}), config=config)
    return _to_response(question, result, thread_id, started)


def pending_approval(thread_id: str, *, graph=None) -> dict[str, Any] | None:
    """The interrupt payload for a parked run, or None if nothing is pending."""
    graph = graph or get_graph()
    snapshot = graph.get_state(_config(thread_id))
    for task in snapshot.tasks or ():
        for pending in getattr(task, "interrupts", ()) or ():
            return dict(pending.value) if isinstance(pending.value, dict) else pending.value
    return None


def _config(thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }


def _to_response(
    question: str, result: dict[str, Any], thread_id: str, started: float
) -> AnswerResponse:
    interrupts = result.get("__interrupt__") or ()
    awaiting = bool(interrupts)

    sql = result.get("sql")
    if awaiting and sql is None:
        # The interrupt fired before the proposal was merged into state.
        payload = interrupts[0].value if isinstance(interrupts[0].value, dict) else {}
        sql = SqlProposal(sql=payload.get("sql", ""), rationale=payload.get("rationale", ""))

    answer = result.get("answer", "")
    if awaiting and not answer:
        answer = "This question needs a database query. Review and approve the SQL to continue."

    trace = list(result.get("trace") or [])
    if awaiting:
        trace.append(TraceStep(node="sql_approval", detail="awaiting human approval"))

    return AnswerResponse(
        question=question,
        answer=answer,
        route=Route(result.get("route", Route.VECTOR)),
        citations=citations_from(result),
        sql=sql,
        awaiting_approval=awaiting,
        thread_id=thread_id,
        cached=bool(result.get("cached")),
        cache_kind=result.get("cache_kind", "none"),
        extractive=bool(result.get("extractive")),
        guardrails=list(result.get("guardrails") or []),
        blocked=bool(result.get("blocked")),
        trace=trace,
        input_tokens=int(result.get("input_tokens", 0)),
        output_tokens=int(result.get("output_tokens", 0)),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
