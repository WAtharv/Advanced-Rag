"""Graph assembly.

The shape of the pipeline is decided here from the feature flags, so a run with
CRAG and Self-RAG disabled is a genuinely smaller graph rather than the same
graph with nodes that no-op. That matters for the lecture-by-lecture progression:
you can turn the course's later layers off and get the baseline back.

    guardrail_input -> cache_lookup -> route
                                        |- vector -> retrieve -> [grade -> rewrite]* -> generate
                                        |- sql/both -> sql_generate -> approval -> execute -> ...
    generate -> [critique -> generate]* -> guardrail_output -> finalize
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from advanced_rag.config import Settings, get_settings
from advanced_rag.graph import nodes
from advanced_rag.graph.state import RagState
from advanced_rag.models import Route, Verdict

logger = logging.getLogger(__name__)

#: Hard ceiling on corrective retrieval passes, independent of the verdict.
MAX_RETRIEVAL_ATTEMPTS = 2


# ------------------------------------------------------------------ conditions


def after_input_guardrails(state: RagState) -> Literal["cache_lookup", "end"]:
    return "end" if state.get("blocked") else "cache_lookup"


def after_cache(state: RagState) -> Literal["route", "end"]:
    return "end" if state.get("cached") else "route"


def after_route(state: RagState) -> Literal["retrieve", "sql_generate", "end"]:
    route = state.get("route", Route.VECTOR)
    if state.get("blocked") or route is Route.REJECT:
        return "end"
    if route in (Route.SQL, Route.BOTH):
        return "sql_generate"
    return "retrieve"


def after_sql_generate(state: RagState) -> Literal["sql_approval", "generate", "retrieve"]:
    proposal = state.get("sql")
    if proposal is None or not proposal.sql:
        # No runnable query: fall back to documents rather than dead-ending.
        return "retrieve" if state.get("route") is Route.BOTH else "generate"
    return "sql_approval"


def after_approval(state: RagState) -> Literal["sql_execute", "guardrail_output"]:
    proposal = state.get("sql")
    if proposal is not None and proposal.approved:
        return "sql_execute"
    return "guardrail_output"


def after_sql_execute(state: RagState) -> Literal["retrieve", "generate"]:
    return "retrieve" if state.get("route") is Route.BOTH else "generate"


def after_grade(state: RagState) -> Literal["generate", "rewrite_query"]:
    verdict = state.get("verdict", Verdict.CORRECT)
    if verdict is Verdict.CORRECT:
        return "generate"
    if state.get("retrieval_attempts", 0) >= MAX_RETRIEVAL_ATTEMPTS:
        # Out of corrective budget - answer from what we have and say it is thin.
        logger.info("Retrieval attempts exhausted; generating with a %s verdict", verdict.value)
        return "generate"
    return "rewrite_query"


def after_critique(state: RagState) -> Literal["generate", "guardrail_output"]:
    settings = get_settings()
    if not state.get("critique"):
        return "guardrail_output"
    if state.get("self_rag_attempts", 0) > settings.self_rag_max_retries:
        logger.info("Self-RAG retry budget exhausted; returning the current draft")
        return "guardrail_output"
    return "generate"


# --------------------------------------------------------------------- builder


def build_graph(settings: Settings | None = None, *, checkpointer=None):
    """Compile the pipeline. A checkpointer is required for SQL approval."""
    settings = settings or get_settings()
    graph = StateGraph(RagState)

    graph.add_node("guardrail_input", nodes.guardrail_input_node)
    graph.add_node("cache_lookup", nodes.cache_lookup_node)
    graph.add_node("route", nodes.route_node)
    graph.add_node("retrieve", nodes.retrieve_node)
    graph.add_node("generate", nodes.generate_node)
    graph.add_node("guardrail_output", nodes.guardrail_output_node)
    graph.add_node("finalize", nodes.finalize_node)

    graph.add_edge(START, "guardrail_input")
    graph.add_conditional_edges(
        "guardrail_input",
        after_input_guardrails,
        {"cache_lookup": "cache_lookup", "end": "finalize"},
    )
    graph.add_conditional_edges(
        "cache_lookup", after_cache, {"route": "route", "end": "finalize"}
    )

    # ---- Text2SQL branch
    if settings.enable_text2sql:
        graph.add_node("sql_generate", nodes.sql_generate_node)
        graph.add_node("sql_approval", nodes.sql_approval_node)
        graph.add_node("sql_execute", nodes.sql_execute_node)
        graph.add_conditional_edges(
            "route",
            after_route,
            {"retrieve": "retrieve", "sql_generate": "sql_generate", "end": "finalize"},
        )
        graph.add_conditional_edges(
            "sql_generate",
            after_sql_generate,
            {"sql_approval": "sql_approval", "generate": "generate", "retrieve": "retrieve"},
        )
        graph.add_conditional_edges(
            "sql_approval",
            after_approval,
            {"sql_execute": "sql_execute", "guardrail_output": "guardrail_output"},
        )
        graph.add_conditional_edges(
            "sql_execute",
            after_sql_execute,
            {"retrieve": "retrieve", "generate": "generate"},
        )
    else:
        graph.add_conditional_edges(
            "route", after_route, {"retrieve": "retrieve", "end": "finalize"}
        )

    # ---- CRAG branch
    if settings.enable_crag:
        graph.add_node("grade_context", nodes.grade_node)
        graph.add_node("rewrite_query", nodes.rewrite_node)
        graph.add_edge("retrieve", "grade_context")
        graph.add_conditional_edges(
            "grade_context",
            after_grade,
            {"generate": "generate", "rewrite_query": "rewrite_query"},
        )
        # A rewrite loops back through retrieval, which re-grades on the way out.
        graph.add_edge("rewrite_query", "retrieve")
    else:
        graph.add_edge("retrieve", "generate")

    # ---- Self-RAG branch
    if settings.enable_self_rag:
        graph.add_node("self_critique", nodes.critique_node)
        graph.add_edge("generate", "self_critique")
        graph.add_conditional_edges(
            "self_critique",
            after_critique,
            {"generate": "generate", "guardrail_output": "guardrail_output"},
        )
    else:
        graph.add_edge("generate", "guardrail_output")

    graph.add_edge("guardrail_output", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())


def _make_checkpointer(settings: Settings):
    """The checkpointer for SQL-approval run state.

    MemorySaver is process-local, so /approve must hit the same replica that
    served /ask. Set CHECKPOINT_BACKEND=postgres (with POSTGRES_DSN and the
    [postgres-checkpoint] extra) to persist it and scale out. Any problem -
    missing package, unreachable DB - falls back to memory rather than failing
    startup, so a misconfiguration degrades to single-replica instead of down.
    """
    if settings.checkpoint_backend != "postgres":
        return MemorySaver()
    if not settings.postgres_dsn:
        logger.warning("CHECKPOINT_BACKEND=postgres but POSTGRES_DSN is empty - using MemorySaver")
        return MemorySaver()
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError:
        logger.warning(
            'langgraph-checkpoint-postgres not installed '
            '(pip install -e ".[postgres-checkpoint]") - using MemorySaver'
        )
        return MemorySaver()
    # PostgresSaver wants a plain psycopg DSN, not the SQLAlchemy +psycopg form.
    conn = settings.postgres_dsn.replace("+psycopg2", "").replace("+psycopg", "")
    try:
        # from_conn_string is a context manager; enter it for the process lifetime
        # (this is a long-lived singleton) so the connection stays open.
        saver = PostgresSaver.from_conn_string(conn).__enter__()
        saver.setup()  # idempotent: creates the checkpoint tables if absent
        logger.info("Graph checkpointer: Postgres (persistent approval state)")
        return saver
    except Exception as exc:
        logger.warning("Postgres checkpointer unavailable (%s) - using MemorySaver", exc)
        return MemorySaver()


@lru_cache
def get_graph():
    """Process-wide compiled graph.

    Checkpointer is chosen by CHECKPOINT_BACKEND; see `_make_checkpointer`.
    """
    return build_graph(checkpointer=_make_checkpointer(get_settings()))
