""""This file is the SQL Safety Layer of the Text-to-SQL system.

Before any AI-generated SQL reaches the database:

Generated SQL
     ↓
normalize()
     ↓
validate()
     ↓
Human Approval
     ↓
execute()
     ↓
render_rows()
    
It ensures:
✅ Read-only queries
✅ No INSERT/UPDATE/DELETE
✅ No multiple statements
✅ Row limits enforced
✅ Transaction rollback protection

## SQL validation and read-only execution.

Two independent gates stand between a generated string and the database:

1. `validate()` - a static check that rejects anything that is not a single
   read-only SELECT. It runs before a human ever sees the query.
2. Human approval - enforced by the graph, which interrupts and will not call
   `execute()` until the proposal comes back approved.

The validator is deliberately conservative: it rejects on suspicion rather than
trying to sanitise, because a rejected query costs one retry and an accepted bad
one costs a production incident.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text

from advanced_rag.config import Settings, get_settings
from advanced_rag.guardrails import patterns
from advanced_rag.text2sql.database import QueryResult, get_engine

logger = logging.getLogger(__name__)

_LEADING_CTE = re.compile(r"^\s*WITH\b", re.I)
_LEADING_SELECT = re.compile(r"^\s*SELECT\b", re.I)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.I)


class SqlRejected(ValueError):                              # 
    """The generated SQL failed static validation and must not run."""


def normalize(sql: str) -> str:                             # Clean LLM-generated SQL: Removes - Markdown fences, Extra spaces, Trailing semicolon
    """Strip markdown fences and the trailing semicolon the model likes to add."""
    cleaned = sql.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*|\s*```$", "", cleaned, flags=re.I).strip()
    return cleaned.rstrip(";").strip()


def validate(sql: str, settings: Settings | None = None) -> str:        # Verify query is safe: 
                                                                        # Checks happen in order:
                                                                        # Check 1: Empty Query -> the generated query was empty
                                                                        # Check 2: Must Start With SELECT -> Rejected: DELETE, UPDATE, DROP 
                                                                        # Check 3: No Multiple Statements -> ";" in query (This blocks SQL injection.)
                                                                        # Check 4: No Dangerous Keywords -> INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE
                                                                        # Check 5: Enforce LIMIT
    """Return the query with an enforced LIMIT, or raise SqlRejected."""
    settings = settings or get_settings()
    query = normalize(sql)
    if not query:
        raise SqlRejected("the generated query was empty")

    if not (_LEADING_SELECT.match(query) or _LEADING_CTE.match(query)):
        raise SqlRejected("only SELECT (or WITH ... SELECT) queries are allowed")       # Prevents execution.

    # A semicolon anywhere but the (already stripped) end means stacked statements.
    if ";" in query:
        raise SqlRejected("multiple statements are not allowed")

    for pattern in patterns.SQL_WRITE_PATTERNS:
        match = pattern.search(query)
        if match:
            raise SqlRejected(f"query contains a forbidden construct: {match.group(0)!r}")

    return _enforce_limit(query, settings.sql_row_limit)


def _enforce_limit(query: str, row_limit: int) -> str:                    # Prevent huge result sets: (assuming row limit = 100)
                                                                          # LIMIT Too Large: 5000 ->> 100; It clamps instead of rejecting.
    found = _LIMIT.search(query)
    if not found:
        return f"{query} LIMIT {row_limit}"
    if int(found.group(1)) > row_limit:
        # Clamp rather than reject: the query is fine, the bound is not.
        logger.info("Clamping LIMIT %s to %d", found.group(1), row_limit)
        return _LIMIT.sub(f"LIMIT {row_limit}", query, count=1)
    return query


def execute(sql: str, settings: Settings | None = None) -> QueryResult:             # Run validated SQL safely.
    """
    Validate Query              Unsafe query never runs.
      ↓
    Open Connection             connect to PostgreSQL or SQLlite 
        ↓
    Start Transaction           Everything runs inside a transaction
        ↓                       PostgreSQL Safety Settings: Stop long-running queries; Huge Cartesian joins won't run forever.
    Execute Query           
        ↓
    Read Results                Read-Only Mode
        ↓
    Rollback                    Runs in: Finally: -> Meaning: Nothing can be committed.
        ↓
    Return Rows
    """
    
    """Run a validated read-only query inside a rolled-back transaction.

    The rollback is belt-and-braces: `validate()` has already rejected writes,
    but a connection that can never commit means a validator bug cannot mutate
    anything either.
    """
    settings = settings or get_settings()
    query = validate(sql, settings)
    engine = get_engine(settings)

    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            if settings.postgres_dsn:
                # Bound the query server-side too, so a cartesian join cannot
                # hold a connection open indefinitely.
                conn.execute(
                    text(f"SET LOCAL statement_timeout = '{settings.sql_timeout_seconds}s'")
                )
                conn.execute(text("SET LOCAL transaction_read_only = on"))
            result = conn.execute(text(query))
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchmany(settings.sql_row_limit + 1)]
        finally:
            transaction.rollback()

    truncated = len(rows) > settings.sql_row_limit
    return QueryResult(                                                                # Stores SQL results.
        columns=columns, rows=rows[: settings.sql_row_limit], truncated=truncated
    )


def render_rows(result: QueryResult, max_rows: int = 25) -> str:            # Convert SQL result into readable text; This text is passed to the LLM.
    """Compact text table for the answer prompt and the UI."""
    if not result.columns:
        return "(no columns)"
    if not result.rows:
        return "(no rows matched)"

    shown = result.rows[:max_rows]
    widths = [len(c) for c in result.columns]
    for row in shown:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(_cell(value)))

    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(result.columns))
    divider = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(_cell(v).ljust(widths[i]) for i, v in enumerate(row)) for row in shown
    ]
    footer = []
    if len(result.rows) > max_rows:
        footer.append(f"... {len(result.rows) - max_rows} more rows")
    if result.truncated:                                                                # If many rows
        footer.append("(result set truncated at the configured row limit)")
    return "\n".join([header, divider, *body, *footer])


def _cell(value: object) -> str:                                                        # Small formatting helper.
    return "NULL" if value is None else str(value)


"""
Example: Generated SQL: SELECT * FROM incidents
Validate
 ↓
Add LIMIT 100
 ↓
Execute
 ↓
Fetch Rows
 ↓
Rollback
 ↓
Return Results
"""

"""
End-to-End Example

User asks:  Show top 5 sev1 incidents

Step 1: LLM generates:

SQL
SELECT *
FROM incidents
WHERE severity='sev1'
LIMIT 5

Step 2: Python; validate()

✅ SELECT
✅ No DROP
✅ No UPDATE
✅ Single statement

Step 3: Human approves query; Approved

Step 4: Python;  execute()
    Runs query inside:
        Read-only transaction

Step 5: Gets: 4 rows

Step 6: Python; render_rows()
    Creates text table.

Step 7
    Passed to RAG generator.
"""

"""
This file acts as the database firewall for Text-to-SQL by cleaning AI-generated SQL, 
validating it as a single read-only SELECT query, enforcing limits, requiring human approval, 
executing it in a rollback-only transaction, and formatting the results for the LLM.
"""