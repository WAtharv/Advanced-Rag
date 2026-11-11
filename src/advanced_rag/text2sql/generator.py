"""Natural language to SQL, with schema grounding and validation retry loop."""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from advanced_rag.config import Settings, get_settings
from advanced_rag.llm import prompts
from advanced_rag.llm.client import LLMClient, get_llm, llm_available
from advanced_rag.models import SqlProposal
from advanced_rag.text2sql.executor import SqlRejected, validate
from advanced_rag.text2sql.security import SqlValidationResult, validate_sql_ast
from advanced_rag.text2sql.schema import (
    get_known_tables,
    get_schema_prompt,
    get_table_columns,
    table_names,
)

logger = logging.getLogger(__name__)


class GeneratedSql(BaseModel):
    sql: str = Field(default="", description="one read-only SELECT statement, or empty if impossible or ambiguous")
    rationale: str = Field(default="", description="one sentence on what the query returns, or why it cannot be answered")
    tables: list[str] = Field(default_factory=list, description="tables the query reads, or empty list")


def _extract_referenced_tables(sql: str) -> list[str]:
    """Extract table names referenced in FROM and JOIN clauses."""
    pattern = r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
    return [match.group(1).lower() for match in re.finditer(pattern, sql, re.IGNORECASE)]


def _extract_table_aliases(sql: str, known_tables: set[str]) -> dict[str, str]:
    """Map table alias -> actual table name, e.g. 'incidents i' or 'clusters AS c'."""
    aliases: dict[str, str] = {}
    pattern = r"\b(?:FROM|JOIN|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)(?:\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?"
    reserved = {
        "where", "on", "join", "inner", "left", "right", "outer", "cross",
        "group", "order", "limit", "set", "as", "using", "select", "from",
        "and", "or", "not", "in", "is", "null", "having", "union", "natural",
    }
    for match in re.finditer(pattern, sql, re.IGNORECASE):
        tbl = match.group(1).lower()
        alias = match.group(2).lower() if match.group(2) else None
        if tbl in known_tables and alias and alias not in reserved:
            aliases[alias] = tbl
    return aliases


def _check_column_references(sql: str, known_tables: set[str], table_columns: dict[str, set[str]]) -> str | None:
    """Check table.column or alias.column references to detect hallucinated columns."""
    aliases = _extract_table_aliases(sql, known_tables)
    ref_pattern = r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b"
    for match in re.finditer(ref_pattern, sql):
        prefix, col = match.group(1).lower(), match.group(2).lower()
        tbl = aliases.get(prefix) or (prefix if prefix in known_tables else None)
        if tbl and tbl in table_columns:
            valid_cols = table_columns[tbl]
            if col != "*" and col not in valid_cols:
                return (
                    f"column '{col}' does not exist on table '{tbl}'. "
                    f"Valid columns: {', '.join(sorted(valid_cols))}"
                )
    return None


def _check_dialect_compatibility(sql: str, dialect: str) -> str | None:
    """Validate dialect-specific syntax before execution."""
    if dialect.lower() == "sqlite":
        if re.search(r"\bILIKE\b", sql, re.IGNORECASE):
            return "SQLite does not support ILIKE; use LIKE or LOWER(column) = LOWER('value')"
        if re.search(r"\bDATE_TRUNC\b", sql, re.IGNORECASE):
            return "SQLite does not support DATE_TRUNC; use strftime('%Y-%m', date_col) or explicit date comparisons"
        if re.search(r"\bINTERVAL\b", sql, re.IGNORECASE):
            return "SQLite does not support INTERVAL; use date('now', '-N days') or explicit ISO date strings ('YYYY-MM-DD')"
        if re.search(r"\bNOW\(\)", sql, re.IGNORECASE):
            return "SQLite does not support NOW(); use date('now') or datetime('now')"
    return None


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

        Grounds generation in the current database schema, verifies table/column
        references, enforces dialect rules, and returns an empty proposal when
        the question is ambiguous or outside the relational schema.
        """
        if not llm_available():
            return SqlProposal(
                sql="",
                rationale="",
                error="Text2SQL needs a language model; none is configured",
            )

        schema = get_schema_prompt(self.settings)
        known = get_known_tables(self.settings)
        table_cols = get_table_columns(self.settings)
        dialect = self.settings.sql_dialect
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
                explanation = (
                    draft.rationale.strip()
                    or "the question cannot be answered from the available schema"
                )
                return SqlProposal(
                    sql="",
                    rationale=draft.rationale,
                    error=explanation,
                )

            # 1. AST security and allowlist validation
            ast_res = validate_sql_ast(draft.sql, dialect=dialect, settings=self.settings)
            if not ast_res.is_valid:
                feedback = ast_res.error or "SQL AST validation failed"
                logger.info("Rejected generated SQL (attempt %d): %s", attempt, feedback)
                continue

            # 2. Check for unknown tables in declared list
            declared_unknown = [t for t in draft.tables if t.lower() not in known]
            if declared_unknown:
                feedback = (
                    f"these tables do not exist in the schema: {', '.join(sorted(declared_unknown))}. "
                    f"Available tables: {', '.join(sorted(known))}"
                )
                logger.info("Rejected generated SQL (attempt %d): %s", attempt, feedback)
                continue

            # Identify valid tables referenced
            valid_tables = ast_res.tables or sorted(
                set(t.lower() for t in draft.tables if t.lower() in known)
            )

            return SqlProposal(
                sql=ast_res.validated_sql or draft.sql,
                rationale=draft.rationale,
                tables=valid_tables,
                read_only=True,
            )

        return SqlProposal(
            sql="",
            rationale="",
            error=f"could not produce a safe query after {max_attempts} attempts: {feedback}",
        )


_generator: SqlGenerator | None = None


def get_sql_generator(settings: Settings | None = None) -> SqlGenerator:
    global _generator
    if _generator is None or (settings is not None and _generator.settings != settings):
        _generator = SqlGenerator(settings=settings)
    return _generator
