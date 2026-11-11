"""SQL AST validation, schema/table/column allowlists, and read-only database security."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from advanced_rag.config import Settings, get_settings

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Allowlists & Security Policy
# -----------------------------------------------------------------------------

OPERATIONAL_SCHEMAS: set[str] = {"public", "main", ""}

OPERATIONAL_TABLES: set[str] = {
    "clusters",
    "nodes",
    "deployments",
    "incidents",
    "slo_breaches",
}

FORBIDDEN_TABLES: set[str] = {
    "document_metadata",
    "ingestion_runs",
    "sqlite_master",
    "sqlite_schema",
    "sqlite_temp_master",
    "sqlite_sequence",
    "pg_catalog",
    "information_schema",
    "pg_tables",
    "pg_views",
    "pg_user",
    "pg_shadow",
}

OPERATIONAL_COLUMNS: dict[str, set[str]] = {
    "clusters": {
        "id",
        "name",
        "region",
        "environment",
        "k8s_version",
        "node_count",
        "created_at",
    },
    "nodes": {
        "id",
        "cluster_id",
        "name",
        "instance_type",
        "status",
        "cpu_cores",
        "memory_gb",
        "last_ready_at",
    },
    "deployments": {
        "id",
        "cluster_id",
        "service",
        "namespace",
        "version",
        "deployed_at",
        "deployed_by",
        "status",
        "duration_seconds",
        "rolled_back",
    },
    "incidents": {
        "id",
        "cluster_id",
        "service",
        "severity",
        "title",
        "root_cause_category",
        "started_at",
        "resolved_at",
        "duration_minutes",
        "customer_impacting",
    },
    "slo_breaches": {
        "id",
        "cluster_id",
        "service",
        "slo_name",
        "target_pct",
        "actual_pct",
        "window_start",
        "window_end",
    },
}

ALLOWED_FUNCTIONS: set[str] = {
    # Aggregates
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "total",
    # String manipulation
    "lower",
    "upper",
    "length",
    "substr",
    "substring",
    "trim",
    "ltrim",
    "rtrim",
    "replace",
    "instr",
    "str_position",
    "strposition",
    "like",
    "ilike",
    # Null & math
    "coalesce",
    "nullif",
    "round",
    "abs",
    "ceil",
    "floor",
    # Dates
    "strftime",
    "date",
    "time",
    "datetime",
    "julianday",
    "now",
    "current_date",
    "currentdate",
    "current_time",
    "currenttime",
    "current_timestamp",
    "currenttimestamp",
    "date_trunc",
    "timestamp_trunc",
    "timestamptrunc",
    "extract",
    "age",
    "to_char",
    "time_to_str",
    "timetostr",
    "to_date",
    "str_to_date",
    "strtodate",
    "to_timestamp",
    "str_to_time",
    "strtotime",
    "ts_or_ds_to_timestamp",
    "tsordstotimestamp",
}

DANGEROUS_FUNCTIONS: set[str] = {
    "load_extension",
    "eval",
    "exec",
    "system",
    "readfile",
    "writefile",
    "edit",
    "schema",
    "char",
    "randomblob",
    "zeroblob",
    "version",
    "pg_read_file",
    "pg_write_file",
    "pg_sleep",
    "query_to_xml",
    "dblink",
    "nextval",
    "setval",
    "set_config",
    "pg_advisory_lock",
}

# AST node types that indicate dangerous or state-mutating operations
DANGEROUS_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Pragma,
    exp.Command,
    exp.Attach,
    exp.Detach,
    exp.TruncateTable,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,
    exp.Lock,
)

_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.I)


@dataclass
class SqlValidationResult:
    """Structured result of AST-based SQL security and allowlist validation."""

    is_valid: bool
    error: str | None = None
    statement_type: str = "SELECT"
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    dialect: str = "sqlite"
    validated_sql: str | None = None


def normalize_sql(sql: str) -> str:
    """Strip markdown fences, leading/trailing whitespace, and trailing semicolons."""
    cleaned = sql.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*|\s*```$", "", cleaned, flags=re.I).strip()
    return cleaned.rstrip(";").strip()


def _enforce_limit(query: str, row_limit: int) -> str:
    """Clamp excessive LIMIT or append default LIMIT."""
    found = _LIMIT.search(query)
    if not found:
        return f"{query} LIMIT {row_limit}"
    if int(found.group(1)) > row_limit:
        logger.info("Clamping LIMIT %s to %d", found.group(1), row_limit)
        return _LIMIT.sub(f"LIMIT {row_limit}", query, count=1)
    return query


def validate_sql_ast(
    sql: str,
    dialect: str | None = None,
    settings: Settings | None = None,
) -> SqlValidationResult:
    """Parse and validate SQL using SQLGlot AST against security rules and schema allowlists.

    Enforces:
    1. Exactly one valid SQL statement.
    2. Read-only SELECT operation (including safe CTEs and nested subqueries).
    3. Rejection of all DDL, DML, PRAGMA, ATTACH, and mutation constructs.
    4. Rejection of unapproved functions and dialect-incompatible syntax.
    5. Schema, table, and column allowlist enforcement.
    6. Row limit bounding.
    """
    settings = settings or get_settings()
    dialect_str = (dialect or settings.sql_dialect).lower()
    glot_dialect = "postgres" if "postgres" in dialect_str else "sqlite"

    cleaned = normalize_sql(sql)
    if not cleaned:
        return SqlValidationResult(
            is_valid=False,
            error="The query was empty",
            dialect=dialect_str,
        )

    # Check for comment smuggling
    if re.search(r"--|/\*|\*/", cleaned):
        return SqlValidationResult(
            is_valid=False,
            error="Comment smuggling constructs (-- or /* */) are not permitted",
            dialect=dialect_str,
        )

    # A semicolon anywhere in the query (apart from already stripped trailing) indicates multiple statements
    if ";" in cleaned:
        return SqlValidationResult(
            is_valid=False,
            error="multiple statements are not allowed",
            dialect=dialect_str,
        )

    # Fail closed if allowlists are missing
    if not OPERATIONAL_TABLES or not OPERATIONAL_COLUMNS:
        return SqlValidationResult(
            is_valid=False,
            error="Security policy error: Operational allowlists are not configured",
            dialect=dialect_str,
        )

    # 1. Parse using SQLGlot
    try:
        parsed_statements = sqlglot.parse(cleaned, read=glot_dialect)
    except ParseError as exc:
        return SqlValidationResult(
            is_valid=False,
            error=f"SQL syntax error: {exc}",
            dialect=dialect_str,
        )
    except Exception as exc:
        return SqlValidationResult(
            is_valid=False,
            error=f"Failed to parse SQL: {exc}",
            dialect=dialect_str,
        )

    # 2. Enforce exactly one statement
    if len(parsed_statements) == 0:
        return SqlValidationResult(
            is_valid=False,
            error="No valid SQL statements found in query",
            dialect=dialect_str,
        )
    if len(parsed_statements) > 1:
        return SqlValidationResult(
            is_valid=False,
            error="Multiple SQL statements are not permitted",
            dialect=dialect_str,
        )

    root = parsed_statements[0]
    if root is None:
        return SqlValidationResult(
            is_valid=False,
            error="Failed to inspect SQL syntax tree",
            dialect=dialect_str,
        )

    # 3. Validate root expression type
    # Root must be a Select or Union of Selects
    is_select = isinstance(root, (exp.Select, exp.Union))
    if not is_select:
        stmt_type = type(root).__name__.upper()
        return SqlValidationResult(
            is_valid=False,
            error=f"Operation '{stmt_type}' is forbidden. Only read-only SELECT queries are allowed.",
            statement_type=stmt_type,
            dialect=dialect_str,
        )

    # 4. Recursive inspection for dangerous nodes anywhere in the tree
    for node in root.walk():
        if isinstance(node, DANGEROUS_NODE_TYPES):
            node_name = type(node).__name__.upper()
            return SqlValidationResult(
                is_valid=False,
                error=f"Forbidden construct '{node_name}' detected in query or subquery.",
                statement_type=node_name,
                dialect=dialect_str,
            )

        # Check function invocations (both Anonymous and built-in Func classes)
        # Note: In SQLGlot, binary operators/connectors (e.g. And, Or) inherit from Func; skip operators.
        if (
            isinstance(node, (exp.Func, exp.Anonymous))
            and not isinstance(node, (exp.Binary, exp.Unary, exp.Connector, exp.Predicate))
        ):
            candidates: set[str] = set()
            if isinstance(node, exp.Anonymous):
                candidates.add(node.name.lower())
            else:
                if hasattr(node, "sql_name") and node.sql_name():
                    candidates.add(node.sql_name().lower())
                candidates.add(type(node).__name__.lower())
                if hasattr(node, "key") and node.key:
                    candidates.add(str(node.key).lower())

            # 1. Reject explicit dangerous functions
            dangerous_match = candidates.intersection(DANGEROUS_FUNCTIONS)
            if dangerous_match:
                fn_name = sorted(dangerous_match)[0]
                return SqlValidationResult(
                    is_valid=False,
                    error=f"Forbidden function '{fn_name}' is not permitted.",
                    dialect=dialect_str,
                )

            # 2. Reject table-valued functions unless explicitly allowed
            if any("generateseries" in c or "unnest" in c for c in candidates):
                return SqlValidationResult(
                    is_valid=False,
                    error="Table-valued functions like generate_series are not permitted.",
                    dialect=dialect_str,
                )

            # 3. Enforce strict allowlist (fail closed)
            if not candidates.intersection(ALLOWED_FUNCTIONS):
                fn_display = sorted(candidates)[0]
                return SqlValidationResult(
                    is_valid=False,
                    error=f"Function '{fn_display}' is not allowed by policy.",
                    dialect=dialect_str,
                )

        # Check SQLite dialect-incompatible constructs
        if glot_dialect == "sqlite":
            if isinstance(node, exp.ILike) or (
                isinstance(node, exp.Anonymous) and node.name.lower() == "ilike"
            ):
                return SqlValidationResult(
                    is_valid=False,
                    error="SQLite does not support ILIKE; use LIKE or LOWER(column) = LOWER('value')",
                    dialect=dialect_str,
                )
            if isinstance(node, exp.Anonymous) and node.name.lower() == "date_trunc":
                return SqlValidationResult(
                    is_valid=False,
                    error="SQLite does not support DATE_TRUNC; use strftime('%Y-%m', date_col)",
                    dialect=dialect_str,
                )
            if isinstance(node, exp.Interval):
                return SqlValidationResult(
                    is_valid=False,
                    error="SQLite does not support INTERVAL; use datetime('now', '-N days')",
                    dialect=dialect_str,
                )

    # 5. Extract and validate CTEs
    cte_names: set[str] = set()
    for cte in root.find_all(exp.CTE):
        alias_or_name = (cte.alias_or_name or "").lower()
        if alias_or_name:
            cte_names.add(alias_or_name)
        # Verify CTE inner expression is a Select
        cte_expr = cte.this
        if not isinstance(cte_expr, (exp.Select, exp.Union)):
            return SqlValidationResult(
                is_valid=False,
                error=f"CTE '{alias_or_name}' contains forbidden non-SELECT statement.",
                dialect=dialect_str,
            )

    # 6. Extract, resolve and validate tables and aliases
    referenced_tables: list[str] = []
    alias_to_table: dict[str, str] = {}

    for tbl_node in root.find_all(exp.Table):
        # Check schema qualification (e.g. schema.table)
        schema_qual = (tbl_node.db or "").lower()
        if schema_qual and schema_qual not in OPERATIONAL_SCHEMAS:
            return SqlValidationResult(
                is_valid=False,
                error=f"Unauthorized schema '{schema_qual}'. Only operational database schemas are allowed.",
                dialect=dialect_str,
            )

        t_name = (tbl_node.name or "").lower()
        if not t_name:
            continue

        # CTE local references are valid
        if t_name in cte_names:
            alias = (tbl_node.alias or "").lower()
            if alias:
                alias_to_table[alias] = t_name
            continue

        # Check explicit forbidden internal tables (document registry, sqlite metadata, pg catalog)
        if t_name in FORBIDDEN_TABLES or any(t_name.startswith(pfx) for pfx in ("sqlite_", "pg_")):
            return SqlValidationResult(
                is_valid=False,
                error=f"Access to internal or system table '{t_name}' is strictly forbidden.",
                dialect=dialect_str,
            )

        # Check operational table allowlist
        if t_name not in OPERATIONAL_TABLES:
            return SqlValidationResult(
                is_valid=False,
                error=f"these tables do not exist in the schema: {t_name}. Available tables: {', '.join(sorted(OPERATIONAL_TABLES))}",
                dialect=dialect_str,
            )

        referenced_tables.append(t_name)
        alias = (tbl_node.alias or "").lower()
        if alias:
            alias_to_table[alias] = t_name

    unique_tables = sorted(set(referenced_tables))
    has_from = root.find(exp.From) is not None
    if has_from and not unique_tables and not cte_names:
        return SqlValidationResult(
            is_valid=False,
            error="Query does not reference any authorized tables",
            dialect=dialect_str,
        )

    # 7. Extract and validate column references
    # Also collect explicit projected column aliases (e.g. SELECT COUNT(*) AS cnt)
    projected_aliases: set[str] = set()
    if isinstance(root, exp.Select):
        projected_aliases = {
            s.alias.lower()
            for s in root.selects
            if isinstance(s, exp.Alias) and s.alias
        }

    referenced_columns: list[str] = []
    for col_node in root.find_all(exp.Column):
        c_name = (col_node.name or "").lower()
        qualifier = (col_node.table or "").lower()

        if c_name == "*":
            continue

        referenced_columns.append(c_name)

        if qualifier:
            # Resolve qualifier: could be alias, physical table name, or CTE name
            resolved_tbl = alias_to_table.get(qualifier, qualifier)
            if resolved_tbl in cte_names:
                # Column is on a CTE, skip physical schema check
                continue
            if resolved_tbl in OPERATIONAL_TABLES:
                valid_cols = OPERATIONAL_COLUMNS.get(resolved_tbl, set())
                if c_name not in valid_cols and c_name not in projected_aliases:
                    return SqlValidationResult(
                        is_valid=False,
                        error=f"column '{c_name}' does not exist on table '{resolved_tbl}'. Valid columns: {', '.join(sorted(valid_cols))}",
                        tables=unique_tables,
                        dialect=dialect_str,
                    )
            else:
                return SqlValidationResult(
                    is_valid=False,
                    error=f"Unknown or unauthorized table identifier '{qualifier}' in column reference.",
                    tables=unique_tables,
                    dialect=dialect_str,
                )
        else:
            # Unqualified column
            if c_name in projected_aliases:
                # Valid projected alias reference in HAVING or ORDER BY
                continue

            if len(unique_tables) == 1 and not cte_names:
                target_tbl = unique_tables[0]
                valid_cols = OPERATIONAL_COLUMNS.get(target_tbl, set())
                if c_name not in valid_cols:
                    return SqlValidationResult(
                        is_valid=False,
                        error=f"column '{c_name}' does not exist on table '{target_tbl}'. Valid columns: {', '.join(sorted(valid_cols))}",
                        tables=unique_tables,
                        dialect=dialect_str,
                    )
            elif len(unique_tables) > 1:
                # Check that column belongs to at least one referenced operational table
                matching = [t for t in unique_tables if c_name in OPERATIONAL_COLUMNS.get(t, set())]
                if not matching and not cte_names:
                    return SqlValidationResult(
                        is_valid=False,
                        error=f"column '{c_name}' does not exist on any referenced operational table.",
                        tables=unique_tables,
                        dialect=dialect_str,
                    )

    # 8. Row limit enforcement
    safe_sql = _enforce_limit(cleaned, settings.sql_row_limit)

    return SqlValidationResult(
        is_valid=True,
        statement_type="SELECT",
        tables=unique_tables,
        columns=sorted(set(referenced_columns)),
        dialect=dialect_str,
        validated_sql=safe_sql,
    )
