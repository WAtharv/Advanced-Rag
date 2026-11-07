"""Schema description fed to the SQL generator.

Introspected from the live database rather than hardcoded, so the prompt can
never drift from the tables that actually exist. The rendered text is cached and
stable, which also makes it a good prompt-cache prefix.
"""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

from sqlalchemy import inspect

from advanced_rag.config import get_settings
from advanced_rag.text2sql.database import COLUMN_NOTES, get_engine

logger = logging.getLogger(__name__)

SAMPLE_QUESTIONS = """Worked examples:

Q: How many sev1 incidents did production clusters have in June 2026?
SQL: SELECT COUNT(*) AS sev1_count FROM incidents i JOIN clusters c ON c.id = i.cluster_id
     WHERE i.severity = 'sev1' AND c.environment = 'production'
     AND i.started_at >= '2026-06-01' AND i.started_at < '2026-07-01' LIMIT 100;

Q: Which services had the most failed deployments?
SQL: SELECT service, COUNT(*) AS failed FROM deployments WHERE status = 'failed'
     GROUP BY service ORDER BY failed DESC LIMIT 10;

Q: What is the average incident duration by root cause?
SQL: SELECT root_cause_category, ROUND(AVG(duration_minutes), 1) AS avg_minutes,
     COUNT(*) AS incidents FROM incidents WHERE duration_minutes IS NOT NULL
     GROUP BY root_cause_category ORDER BY avg_minutes DESC LIMIT 20;"""


@lru_cache
def get_schema_prompt() -> str:
    """Render CREATE-TABLE-like text for every table, with column notes."""
    settings = get_settings()
    inspector = inspect(get_engine())
    tables = sorted(inspector.get_table_names())
    if not tables:
        logger.warning("Operations database is empty - run `rag-ingest --seed-sql`")
        return "(the operations database has no tables yet)"

    blocks: list[str] = []
    for table in tables:
        lines = [f"TABLE {table} ("]
        for column in inspector.get_columns(table):
            note = COLUMN_NOTES.get(f"{table}.{column['name']}")
            entry = f"  {column['name']} {column['type']}"
            lines.append(entry + (f"  -- {note}" if note else ""))
        lines.append(")")
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table"):
                lines.append(
                    f"  FOREIGN KEY ({', '.join(fk['constrained_columns'])}) -> "
                    f"{fk['referred_table']}({', '.join(fk['referred_columns'])})"
                )
        blocks.append("\n".join(lines))

    # Resolved once per process, not per request: the date is needed for relative
    # ranges ("last week"), but a value that changes mid-run would invalidate the
    # prompt cache on every call.
    today = date.today().isoformat()
    return (
        f"Dialect: {settings.sql_dialect}\n"
        f"Row limit: every query must end with LIMIT {settings.sql_row_limit} or less.\n"
        f"Today's date: {today}.\n\n" + "\n\n".join(blocks) + "\n\n" + SAMPLE_QUESTIONS
    )


def table_names() -> list[str]:
    return sorted(inspect(get_engine()).get_table_names())
