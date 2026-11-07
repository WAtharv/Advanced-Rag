"""Natural language to SQL, with a validation retry loop."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from advanced_rag.config import Settings, get_settings
from advanced_rag.llm import prompts
from advanced_rag.llm.client import LLMClient, get_llm, llm_available
from advanced_rag.models import SqlProposal
from advanced_rag.text2sql.executor import SqlRejected, validate
from advanced_rag.text2sql.schema import get_schema_prompt, table_names

logger = logging.getLogger(__name__)


class GeneratedSql(BaseModel):
    sql: str = Field(description="one read-only SELECT statement, or empty if impossible")
    rationale: str = Field(description="one sentence on what the query returns, or why not")
    tables: list[str] = Field(description="tables the query reads")


class SqlGenerator:
    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm = llm

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def generate(self, question: str, *, max_attempts: int = 2) -> SqlProposal:
        """Draft SQL and re-prompt with the validator's complaint if it is rejected.

        One retry is usually enough: the failures that survive a second attempt
        are questions the schema cannot answer, not syntax slips.
        """
        if not llm_available():
            # The router sends everything to vector retrieval offline, so
            # this is defensive; a caller that forces route=sql gets a
            # stated reason rather than a traceback.
            return SqlProposal(
                sql="",
                rationale="",
                error="Text2SQL needs a language model; none is configured",
            )

        schema = get_schema_prompt()
        known = set(table_names())
        feedback = ""

        for attempt in range(1, max_attempts + 1):
            prompt = (
                "Database schema:\n" + schema
                + "\n\nQuestion: " + question
                + (("\n\nYour previous attempt was rejected: " + feedback) if feedback else "")
            )
            try:
                draft = self.llm.complete_json(
                    prompt,
                    GeneratedSql,
                    system=prompts.TEXT2SQL_SYSTEM,
                    model=self.settings.llm_model,
                    effort="medium",
                    max_tokens=2_000,
                )
            except Exception as exc:
                logger.exception("SQL generation failed on attempt %d", attempt)
                return SqlProposal(sql="", rationale="", error=f"SQL generation failed: {exc}")

            if not draft.sql.strip():
                return SqlProposal(
                    sql="",
                    rationale=draft.rationale,
                    error="the question cannot be answered from the available schema",
                )

            unknown = [t for t in draft.tables if t not in known]
            if unknown:
                feedback = f"these tables do not exist: {', '.join(unknown)}"
                logger.info("Rejected generated SQL: %s", feedback)
                continue

            try:
                safe_sql = validate(draft.sql, self.settings)
            except SqlRejected as exc:
                feedback = str(exc)
                logger.info("Rejected generated SQL: %s", feedback)
                continue

            return SqlProposal(
                sql=safe_sql,
                rationale=draft.rationale,
                tables=[t for t in draft.tables if t in known],
                read_only=True,
            )

        return SqlProposal(
            sql="",
            rationale="",
            error=f"could not produce a safe query after {max_attempts} attempts: {feedback}",
        )


_generator: SqlGenerator | None = None


def get_sql_generator() -> SqlGenerator:
    global _generator
    if _generator is None:
        _generator = SqlGenerator()
    return _generator
