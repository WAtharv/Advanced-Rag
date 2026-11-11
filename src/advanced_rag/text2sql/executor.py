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

import hashlib
import logging
import re
import sqlite3
import threading
import time
from datetime import date, datetime
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlglot import exp

from advanced_rag.config import Settings, get_settings
from advanced_rag.text2sql.database import QueryResult, get_readonly_engine
from advanced_rag.text2sql.security import normalize_sql, validate_sql_ast

logger = logging.getLogger(__name__)

_LEADING_CTE = re.compile(r"^\s*WITH\b", re.I)
_LEADING_SELECT = re.compile(r"^\s*SELECT\b", re.I)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.I)


class SqlExecutionError(Exception):
    """Base exception for all SQL execution failures."""


class SqlRejected(ValueError, SqlExecutionError):
    """The generated SQL failed static validation and must not run."""


class SqlTimeoutError(SqlExecutionError):
    """Raised when a SQL query exceeds its execution deadline."""


class SqlCancelledError(SqlExecutionError):
    """Raised when a SQL query execution is cancelled."""


class SqlResourceExceededError(SqlExecutionError):
    """Raised when a SQL query exceeds result size or concurrency limits."""


def compute_sql_hash(sql: str) -> str:
    """Compute a deterministic, whitespace-normalized SHA-256 hash for a SQL query."""
    normalized = re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_concurrency_lock = threading.Lock()
_query_semaphore: threading.BoundedSemaphore | None = None


def _get_semaphore(max_concurrent: int) -> threading.BoundedSemaphore:
    global _query_semaphore
    with _concurrency_lock:
        if _query_semaphore is None or getattr(_query_semaphore, "_initial_value", None) != max_concurrent:
            _query_semaphore = threading.BoundedSemaphore(value=max_concurrent)
        return _query_semaphore


def normalize(sql: str) -> str:
    """Strip markdown fences and the trailing semicolon the model likes to add."""
    return normalize_sql(sql)


def validate(sql: str, settings: Settings | None = None) -> str:
    """Validate query safety using AST analysis and allowlists, or raise SqlRejected."""
    settings = settings or get_settings()
    res = validate_sql_ast(sql, dialect=settings.sql_dialect, settings=settings)
    if not res.is_valid:
        raise SqlRejected(res.error or "the generated query failed security validation")
    return res.validated_sql or sql


def _enforce_limit(query: str, row_limit: int) -> str:                    # Prevent huge result sets: (assuming row limit = 100)
    found = _LIMIT.search(query)
    if not found:
        return f"{query} LIMIT {row_limit}"
    if int(found.group(1)) > row_limit:
        logger.info("Clamping LIMIT %s to %d", found.group(1), row_limit)
        return _LIMIT.sub(f"LIMIT {row_limit}", query, count=1)
    return query


def paginate_sql(
    sql: str,
    page: int = 1,
    page_size: int = 50,
    dialect: str = "sqlite",
    max_page_size: int = 1000,
) -> tuple[str, int, int, int]:
    """Safely apply dialect-aware bounded pagination using SQLGlot AST manipulation.

    Returns (paginated_sql, valid_page, valid_page_size, offset).
    Guarantees no raw string concatenation, clamps page size, and prevents negative offsets.
    """
    clean_sql = normalize_sql(sql)
    glot_dialect = "postgres" if dialect in ("postgres", "postgresql") else "sqlite"
    try:
        parsed = sqlglot.parse_one(clean_sql, read=glot_dialect)
    except Exception as exc:
        raise SqlRejected(f"Unable to parse SQL for pagination: {exc}") from exc

    if not parsed or not (isinstance(parsed, exp.Select) or parsed.find(exp.Select)):
        raise SqlRejected("Pagination only allowed on valid SELECT statements")

    valid_page = max(1, int(page))
    valid_page_size = max(1, min(int(page_size), max_page_size))
    offset = (valid_page - 1) * valid_page_size

    # Check if base query has an existing LIMIT
    existing_limit = parsed.args.get("limit")
    if existing_limit is not None:
        try:
            expr_val = existing_limit.expression
            if isinstance(expr_val, exp.Literal) and expr_val.is_int:
                lim_val = int(expr_val.this)
                if lim_val < valid_page_size:
                    valid_page_size = lim_val
        except Exception:
            pass

    transformed = parsed.limit(valid_page_size).offset(offset)
    paginated_sql = transformed.sql(dialect=glot_dialect)
    return paginated_sql, valid_page, valid_page_size, offset


def _infer_column_types(
    columns: list[str], rows: list[list[Any]], cursor: Any = None
) -> dict[str, str]:
    """Infer high-level SQL standard types for returned columns."""
    types: dict[str, str] = {}
    desc = getattr(cursor, "description", None)
    for i, col in enumerate(columns):
        inferred = None
        if desc and i < len(desc) and desc[i] and len(desc[i]) > 1:
            raw_type = desc[i][1]
            if raw_type is not None:
                t_str = getattr(raw_type, "__name__", str(raw_type)).upper()
                if "INT" in t_str:
                    inferred = "INTEGER"
                elif any(k in t_str for k in ("FLOAT", "REAL", "NUM", "DEC")):
                    inferred = "FLOAT"
                elif "BOOL" in t_str:
                    inferred = "BOOLEAN"
                elif any(k in t_str for k in ("DATE", "TIME")):
                    inferred = "TIMESTAMP"
                elif any(k in t_str for k in ("STR", "TEXT", "CHAR", "VARCHAR")):
                    inferred = "TEXT"
                elif "JSON" in t_str:
                    inferred = "JSON"

        if not inferred:
            for row in rows:
                if i < len(row) and row[i] is not None:
                    val = row[i]
                    if isinstance(val, bool):
                        inferred = "BOOLEAN"
                    elif isinstance(val, int):
                        inferred = "INTEGER"
                    elif isinstance(val, float):
                        inferred = "FLOAT"
                    elif isinstance(val, (datetime, date)):
                        inferred = "TIMESTAMP"
                    elif isinstance(val, (dict, list)):
                        inferred = "JSON"
                    elif isinstance(val, bytes):
                        inferred = "BLOB"
                    elif isinstance(val, str):
                        is_iso_ts = (
                            len(val) >= 19
                            and ("T" in val or " " in val)
                            and ("-" in val or ":" in val)
                        )
                        if is_iso_ts:
                            inferred = "TIMESTAMP"
                        else:
                            inferred = "TEXT"
                    break
        types[col] = inferred or "TEXT"
    return types


def generate_sre_summary(result: QueryResult, tables: list[str] | None = None) -> str:
    """Generate deterministic, human-readable SRE summaries without LLM calls."""
    t_names = [t.lower() for t in (tables or [])]
    count = result.row_count
    if count == 0:
        if result.truncated and result.truncation_reason:
            return f"Query returned 0 rows ({result.truncation_reason})."
        return "No records found matching query."

    if len(t_names) == 1:
        t = t_names[0]
        if "cluster" in t:
            entity = f"{count} cluster" if count == 1 else f"{count} clusters"
        elif "node" in t:
            entity = f"{count} node" if count == 1 else f"{count} nodes"
        elif "deployment" in t:
            entity = f"{count} deployment" if count == 1 else f"{count} deployments"
        elif "incident" in t:
            entity = f"{count} incident" if count == 1 else f"{count} incidents"
        elif "slo_breach" in t or "slo" in t:
            entity = f"{count} SLO breach" if count == 1 else f"{count} SLO breaches"
        else:
            entity = f"{count} record" if count == 1 else f"{count} records"
        msg = f"Found {entity}."
    elif len(t_names) > 1:
        msg = f"Returned {count} records across {len(t_names)} tables ({', '.join(t_names)})."
    else:
        msg = f"Returned {count} record{'s' if count != 1 else ''}."

    if result.truncated and result.truncation_reason:
        msg += f" (Notice: {result.truncation_reason})"
    if result.execution_duration_ms > 0:
        msg += f" [Executed in {result.execution_duration_ms:.1f}ms]"
    return msg


def execute(
    sql: str,
    settings: Settings | None = None,
    timeout_seconds: float | None = None,
    page: int | None = None,
    page_size: int | None = None,
    tables: list[str] | None = None,
) -> QueryResult:
    """Revalidate approved SQL and execute via dedicated read-only database connection inside a rolled-back transaction.

    Four independent layers protect the database:
    1. Revalidation: AST parser and allowlist check runs again on the approved SQL.
    2. Concurrency & Resources: Bounded execution semaphore and result cell limits.
    3. Connection & Timeout: Dedicated read-only engine with SQLite progress handler or PostgreSQL statement timeout.
    4. Transaction: Rollback in finally block prevents any mutation from committing.
    """
    settings = settings or get_settings()
    validated_sql = validate(sql, settings)
    engine = get_readonly_engine(settings)
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else float(settings.sql_timeout_seconds)
    )

    # Dialect-aware pagination if requested
    if page is not None or page_size is not None:
        p_val = page if page is not None else 1
        ps_val = page_size if page_size is not None else 50
        query, eff_page, eff_page_size, offset = paginate_sql(
            validated_sql, page=p_val, page_size=ps_val, dialect=settings.sql_dialect
        )
        fetch_limit = eff_page_size + 1
    else:
        eff_page = 1
        eff_page_size = settings.sql_row_limit
        offset = 0
        fetch_limit = settings.sql_row_limit + 1
        query = validated_sql

    sem = _get_semaphore(settings.sql_max_concurrent_queries)
    acquired = sem.acquire(blocking=True, timeout=5.0)
    if not acquired:
        raise SqlResourceExceededError(
            f"Concurrency limit ({settings.sql_max_concurrent_queries}) reached for SQL "
            "execution. Please retry shortly."
        )

    dbapi_conn = None
    t0 = time.monotonic()
    cursor_ref = None
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                # 1. Configure dialect-specific execution timeout
                is_pg = (
                    settings.sql_dialect == "postgres"
                    or bool(settings.postgres_readonly_dsn)
                    or bool(settings.postgres_dsn)
                )
                if is_pg:
                    conn.execute(
                        text(f"SET LOCAL statement_timeout = '{int(timeout * 1000)}ms'")
                    )
                    conn.execute(text("SET LOCAL transaction_read_only = on"))
                else:
                    # SQLite progress handler for execution deadline
                    raw_conn = getattr(conn.connection, "dbapi_connection", None)
                    if raw_conn is not None and hasattr(raw_conn, "set_progress_handler"):
                        dbapi_conn = raw_conn
                        deadline = time.monotonic() + timeout

                        def _sqlite_progress() -> int:
                            if time.monotonic() > deadline:
                                return 1  # Interrupts query with sqlite3.OperationalError: interrupted
                            return 0

                        dbapi_conn.set_progress_handler(_sqlite_progress, 100)

                # 2. Execute query
                result = conn.execute(text(query))
                cursor_ref = getattr(result, "cursor", None)
                columns = list(result.keys())
                raw_rows = [list(row) for row in result.fetchmany(fetch_limit)]

            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc).lower():
                    logger.warning("SQLite query timed out after %.2fs: %s", timeout, query[:80])
                    raise SqlTimeoutError(
                        f"SQL execution timed out after {timeout:.1f}s (interrupted)"
                    ) from exc
                raise
            except Exception as exc:
                err_msg = str(exc).lower()
                if (
                    "timeout" in err_msg
                    or "statement timeout" in err_msg
                    or "query_canceled" in err_msg
                    or "interrupted" in err_msg
                ):
                    logger.warning("Query timed out after %.2fs: %s", timeout, query[:80])
                    raise SqlTimeoutError(f"SQL execution timed out after {timeout:.1f}s") from exc
                raise
            finally:
                # Clean up SQLite progress handler so pooled connection is clean
                if dbapi_conn is not None and hasattr(dbapi_conn, "set_progress_handler"):
                    try:
                        dbapi_conn.set_progress_handler(None, 0)
                    except Exception:
                        pass
                transaction.rollback()
    finally:
        sem.release()

    duration_ms = (time.monotonic() - t0) * 1000.0

    # Clean serializable cell values (e.g. format dates)
    cleaned_rows: list[list[Any]] = []
    for row in raw_rows:
        clean_row = []
        for cell in row:
            if isinstance(cell, (datetime, date)):
                clean_row.append(cell.isoformat())
            else:
                clean_row.append(cell)
        cleaned_rows.append(clean_row)

    # 3. Enforce pagination bounds, row limits and cell budget
    has_more = len(cleaned_rows) > eff_page_size
    rows = cleaned_rows[:eff_page_size]
    truncated = has_more
    truncation_reason = f"More than {len(rows)} row(s) available; paginated." if has_more else None

    max_cells = getattr(settings, "sql_max_result_cells", 5000)
    num_cols = len(columns)

    if num_cols > max_cells:
        # A single row exceeds the entire cell budget
        rows = []
        truncated = True
        truncation_reason = (
            f"Result cell budget ({max_cells}) exceeded by single row width ({num_cols} columns). 0 rows returned."
        )
    elif len(rows) * num_cols > max_cells and num_cols > 0:
        max_rows_allowed = max_cells // num_cols
        rows = rows[:max_rows_allowed]
        truncated = True
        truncation_reason = (
            f"Result cell budget ({max_cells}) reached; truncated to {len(rows)} rows."
        )

    col_types = _infer_column_types(columns, rows, cursor_ref)

    res = QueryResult(
        columns=columns,
        rows=rows,
        truncated=truncated,
        column_types=col_types,
        truncation_reason=truncation_reason,
        has_more=has_more,
        page=eff_page,
        page_size=eff_page_size,
        offset=offset,
        execution_duration_ms=duration_ms,
    )
    res.summary = generate_sre_summary(res, tables=tables)
    return res


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