"""Schema description fed to the SQL generator.

Introspected from the live database rather than hardcoded, so the prompt can
never drift from the tables that actually exist. The rendered text is cached and
stable, which also makes it a good prompt-cache prefix.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import inspect

from advanced_rag.config import Settings, get_settings
from advanced_rag.text2sql.database import (
    COLUMN_NOTES,
    RELATIONSHIP_NOTES,
    TABLE_NOTES,
    get_engine,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

OPERATIONAL_TABLES = ("clusters", "nodes", "deployments", "incidents", "slo_breaches")

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
     GROUP BY root_cause_category ORDER BY avg_minutes DESC LIMIT 20;

Q: What is the total CPU and memory capacity across Ready nodes in production clusters?
SQL: SELECT c.name AS cluster_name, SUM(n.cpu_cores) AS total_cpu_cores, SUM(n.memory_gb) AS total_memory_gb
     FROM nodes n JOIN clusters c ON c.id = n.cluster_id
     WHERE n.status = 'Ready' AND c.environment = 'production'
     GROUP BY c.name ORDER BY total_cpu_cores DESC LIMIT 20;

Q: How many customer-impacting incidents were caused by bad deployments?
SQL: SELECT COUNT(*) AS incident_count FROM incidents
     WHERE customer_impacting = 1 AND root_cause_category = 'bad_deploy' LIMIT 100;

Q: List all currently open or active incidents.
SQL: SELECT id, service, severity, title, started_at FROM incidents
     WHERE resolved_at IS NULL ORDER BY started_at DESC LIMIT 50;

Q: Which services experienced the largest SLO breaches?
SQL: SELECT service, slo_name, target_pct, actual_pct, ROUND(target_pct - actual_pct, 2) AS gap_pct
     FROM slo_breaches WHERE actual_pct < target_pct ORDER BY gap_pct DESC LIMIT 20;

Q: How many deployments were rolled back in the last 30 days?
SQL: SELECT service, namespace, version, deployed_at, deployed_by FROM deployments
     WHERE rolled_back = 1 AND deployed_at >= date('now', '-30 days')
     ORDER BY deployed_at DESC LIMIT 50;

Q: Show me the pod logs or container metrics for payment-service.
SQL:
Rationale: The operations database tracks clusters, nodes, deployments, incidents, and SLO breaches, but does not contain real-time container metrics or pod log streams."""

_schema_cache: dict[tuple[str, int, str], str] = {}


def get_schema_prompt(settings: Settings | None = None) -> str:
    """Render CREATE-TABLE-like text for every operational table, with column notes and dialect rules."""
    settings = settings or get_settings()
    engine = get_engine(settings)
    engine_url = str(engine.url)
    cache_key = (settings.sql_dialect, settings.sql_row_limit, engine_url)

    if cache_key in _schema_cache:
        return _schema_cache[cache_key]

    inspector = inspect(engine)
    all_tables = inspector.get_table_names()
    if not all_tables:
        logger.warning("Operations database is empty - run `rag-ingest --seed-sql`")
        return "(the operations database has no tables yet)"

    # Prioritize operations domain tables over internal registry tables
    tables = [t for t in OPERATIONAL_TABLES if t in all_tables] or sorted(all_tables)

    blocks: list[str] = []
    for table in tables:
        pk_constraint = inspector.get_pk_constraint(table)
        pk_cols = set(pk_constraint.get("constrained_columns", []) if pk_constraint else [])
        table_note = TABLE_NOTES.get(table, "")
        header = f"TABLE {table} (" + (f"  -- {table_note}" if table_note else "")
        lines = [header]

        for column in inspector.get_columns(table):
            col_name = column["name"]
            col_type = column["type"]
            note = COLUMN_NOTES.get(f"{table}.{col_name}")
            pk_str = " [PRIMARY KEY]" if col_name in pk_cols else ""
            nn_str = " NOT NULL" if not column.get("nullable", True) else ""
            entry = f"  {col_name} {col_type}{pk_str}{nn_str}"
            lines.append(entry + (f"  -- {note}" if note else ""))

        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table"):
                lines.append(
                    f"  FOREIGN KEY ({', '.join(fk['constrained_columns'])}) REFERENCES "
                    f"{fk['referred_table']}({', '.join(fk['referred_columns'])})"
                )
        lines.append(")")
        blocks.append("\n".join(lines))

    rel_block = "Key Relationships:\n" + "\n".join(f"- {r}" for r in RELATIONSHIP_NOTES)

    dialect = settings.sql_dialect
    if dialect.lower() == "sqlite":
        dialect_rules = (
            "Dialect Specifics (SQLite):\n"
            "- Use standard SQLite-compatible syntax.\n"
            "- Case-insensitive text comparison: Use LIKE or LOWER(column) = LOWER('value'). Do NOT use ILIKE.\n"
            "- Date filtering: Dates are stored as ISO strings ('YYYY-MM-DD'). Use string comparisons ('started_at >= 2026-06-01') "
            "or SQLite date helpers: date('now', '-30 days'), strftime('%Y-%m', date_col). Do NOT use NOW(), INTERVAL, or DATE_TRUNC.\n"
            "- Boolean flags: Stored as integers. Use '= 1' for true, '= 0' for false (e.g. rolled_back = 1, customer_impacting = 1)."
        )
    else:
        dialect_rules = (
            "Dialect Specifics (PostgreSQL):\n"
            "- Use standard PostgreSQL-compatible syntax.\n"
            "- Case-insensitive text comparison: ILIKE or LOWER(column) = LOWER('value') are supported.\n"
            "- Date filtering: Use standard PostgreSQL date functions: CURRENT_DATE, INTERVAL '30 days', DATE_TRUNC('month', date_col).\n"
            "- Boolean flags: Stored as integers (0/1) or boolean expressions. Prefer '= 1' or '= 0' for consistency with schema."
        )

    ambiguity_rules = (
        "Ambiguity & Grounding Rules:\n"
        "- Grounding: Use ONLY tables and columns declared above. Never query non-existent tables or invent column names.\n"
        "- Relationships: Join child tables on clusters.id (e.g. incidents.cluster_id = clusters.id).\n"
        "- Ambiguous or Unsupported Questions: If the question cannot be answered from the schema (e.g. asks for pod logs, "
        "raw metrics, source code, or unrecorded fields), or is too vague to determine a specific query, return an empty "
        "sql string (\"\") and explain the limitation in the rationale."
    )

    today = date.today().isoformat()
    rendered = (
        f"Dialect: {dialect}\n"
        f"Row limit: every query must end with LIMIT {settings.sql_row_limit} or less.\n"
        f"Today's date: {today}.\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + rel_block
        + "\n\n"
        + dialect_rules
        + "\n\n"
        + ambiguity_rules
        + "\n\n"
        + SAMPLE_QUESTIONS
    )

    _schema_cache[cache_key] = rendered
    return rendered


def table_names(settings: Settings | None = None) -> list[str]:
    """Return operational table names available in the database."""
    inspector = inspect(get_engine(settings))
    all_tables = inspector.get_table_names()
    ops = [t for t in OPERATIONAL_TABLES if t in all_tables]
    return ops or sorted(all_tables)


def get_known_tables(settings: Settings | None = None) -> set[str]:
    """Return set of known operational table names."""
    return set(table_names(settings))


def get_table_columns(settings: Settings | None = None) -> dict[str, set[str]]:
    """Return mapping of table_name -> set of lowercased column names."""
    inspector = inspect(get_engine(settings))
    result: dict[str, set[str]] = {}
    for table in table_names(settings):
        result[table.lower()] = {col["name"].lower() for col in inspector.get_columns(table)}
    return result
