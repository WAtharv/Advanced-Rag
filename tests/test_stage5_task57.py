"""Stage 5 Task 57 — Natural-Language-to-SQL Generation Improvement Tests.

Comprehensive tests verifying:
1. Simple SELECT generation
2. WHERE filtering
3. ORDER BY
4. GROUP BY and aggregation
5. Date-based filtering
6. JOIN generation
7. Invalid table or column references (with retry feedback)
8. Ambiguous natural-language questions and unsupported schema requests
9. SQLite dialect compatibility (rejecting ILIKE/INTERVAL on SQLite)
10. PostgreSQL dialect-aware generation
11. Preservation of the existing approval workflow (approved=False, interrupt)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from advanced_rag.config import Settings
from advanced_rag.models import SqlProposal
from advanced_rag.text2sql.generator import (
    GeneratedSql,
    SqlGenerator,
    _check_column_references,
    _check_dialect_compatibility,
    _extract_referenced_tables,
    _extract_table_aliases,
)
from advanced_rag.text2sql.schema import (
    get_known_tables,
    get_schema_prompt,
    get_table_columns,
    table_names,
)


class MockLLM:
    """Mock LLM that returns pre-configured GeneratedSql responses."""

    def __init__(self, responses: list[GeneratedSql] | None = None) -> None:
        self.responses = list(responses or [])
        self.call_history: list[dict] = []

    def complete_json(self, prompt: str, schema_model, **kwargs):
        self.call_history.append({"prompt": prompt, "kwargs": kwargs})
        if self.responses:
            return self.responses.pop(0)
        return GeneratedSql(sql="", rationale="No response configured", tables=[])


@pytest.fixture
def sqlite_settings() -> Settings:
    return Settings(postgres_dsn=None, sql_row_limit=100)


@pytest.fixture
def pg_settings() -> Settings:
    return Settings(
        postgres_dsn="postgresql+psycopg://user:password12345678@localhost:5432/ops",
        sql_row_limit=100,
    )


# ------------------------------------------------------------- 1. Simple SELECT


def test_simple_select_generation(sqlite_settings):
    """Test standard SELECT statement generation."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql="SELECT name, region FROM clusters",
                rationale="lists cluster names and regions",
                tables=["clusters"],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("List all cluster names and regions")

    assert isinstance(proposal, SqlProposal)
    assert proposal.sql == "SELECT name, region FROM clusters LIMIT 100"
    assert proposal.read_only is True
    assert proposal.approved is False
    assert "clusters" in proposal.tables
    assert proposal.error is None
    assert proposal.rationale == "lists cluster names and regions"


# ------------------------------------------------------------- 2. WHERE Filtering


def test_where_filtering_generation(sqlite_settings):
    """Test query with WHERE clause filtering."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql="SELECT * FROM incidents WHERE severity = 'sev1' AND customer_impacting = 1",
                rationale="finds customer-impacting sev1 incidents",
                tables=["incidents"],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Show all sev1 incidents that affected customers")

    assert "WHERE severity = 'sev1' AND customer_impacting = 1" in proposal.sql
    assert proposal.sql.endswith("LIMIT 100")
    assert proposal.read_only is True
    assert proposal.approved is False


# ------------------------------------------------------------- 3. ORDER BY


def test_order_by_generation(sqlite_settings):
    """Test query with ORDER BY sorting and LIMIT."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql="SELECT name, node_count FROM clusters ORDER BY node_count DESC",
                rationale="clusters sorted by largest node count",
                tables=["clusters"],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Which clusters have the most nodes?")

    assert "ORDER BY node_count DESC" in proposal.sql
    assert proposal.sql.endswith("LIMIT 100")


# ------------------------------------------------ 4. GROUP BY and Aggregation


def test_group_by_and_aggregation(sqlite_settings):
    """Test query with GROUP BY and aggregate functions (COUNT, AVG)."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql=(
                    "SELECT root_cause_category, COUNT(*) AS total_incidents, "
                    "ROUND(AVG(duration_minutes), 1) AS avg_duration "
                    "FROM incidents WHERE duration_minutes IS NOT NULL "
                    "GROUP BY root_cause_category ORDER BY total_incidents DESC"
                ),
                rationale="incident metrics grouped by root cause category",
                tables=["incidents"],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("What is the average duration and incident count by root cause?")

    assert "GROUP BY root_cause_category" in proposal.sql
    assert "AVG(duration_minutes)" in proposal.sql
    assert "COUNT(*)" in proposal.sql
    assert proposal.sql.endswith("LIMIT 100")


# ------------------------------------------------------ 5. Date-based Filtering


def test_date_based_filtering(sqlite_settings):
    """Test date-based range filtering in ISO-8601 format."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql=(
                    "SELECT service, title, started_at FROM incidents "
                    "WHERE started_at >= '2026-06-01' AND started_at < '2026-07-01'"
                ),
                rationale="incidents that started in June 2026",
                tables=["incidents"],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Show all incidents that started in June 2026")

    assert "started_at >= '2026-06-01'" in proposal.sql
    assert "started_at < '2026-07-01'" in proposal.sql
    assert proposal.sql.endswith("LIMIT 100")


# ------------------------------------------------------------ 6. JOIN Generation


def test_join_generation(sqlite_settings):
    """Test multi-table JOIN query linking on foreign keys."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql=(
                    "SELECT i.id, i.service, i.severity, c.name AS cluster_name, c.environment "
                    "FROM incidents i JOIN clusters c ON c.id = i.cluster_id "
                    "WHERE c.environment = 'production' AND i.severity = 'sev1'"
                ),
                rationale="production sev1 incidents with cluster metadata",
                tables=["incidents", "clusters"],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Find production sev1 incidents and their clusters")

    assert "JOIN clusters c ON c.id = i.cluster_id" in proposal.sql
    assert "c.environment = 'production'" in proposal.sql
    assert set(proposal.tables) == {"incidents", "clusters"}
    assert proposal.read_only is True


# ------------------------------------ 7. Invalid Table or Column with Feedback


def test_invalid_table_rejected_and_retried(sqlite_settings):
    """Test that referencing a nonexistent table prompts feedback and retries."""
    mock_llm = MockLLM(
        [
            # Attempt 1: Hallucinates nonexistent table 'pods'
            GeneratedSql(
                sql="SELECT * FROM pods WHERE status = 'CrashLoopBackOff'",
                rationale="querying pods table",
                tables=["pods"],
            ),
            # Attempt 2: After feedback, uses valid table 'nodes'
            GeneratedSql(
                sql="SELECT * FROM nodes WHERE status = 'NotReady'",
                rationale="querying nodes table instead",
                tables=["nodes"],
            ),
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Show pods in bad status")

    # Should have called LLM twice
    assert len(mock_llm.call_history) == 2
    # Second attempt's prompt should contain feedback about the invalid table
    assert "these tables do not exist in the schema: pods" in mock_llm.call_history[1]["prompt"]
    # Final proposal should be the corrected query
    assert "FROM nodes" in proposal.sql
    assert "nodes" in proposal.tables
    assert proposal.read_only is True


def test_invalid_column_rejected_and_retried(sqlite_settings):
    """Test that referencing a nonexistent column on a known table triggers retry."""
    mock_llm = MockLLM(
        [
            # Attempt 1: Hallucinates 'pod_ip' on incidents table
            GeneratedSql(
                sql="SELECT i.pod_ip, i.service FROM incidents i",
                rationale="querying incident pod ips",
                tables=["incidents"],
            ),
            # Attempt 2: Uses valid columns
            GeneratedSql(
                sql="SELECT i.service, i.title FROM incidents i",
                rationale="querying incident title",
                tables=["incidents"],
            ),
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Show incident pod IPs")

    assert len(mock_llm.call_history) == 2
    assert "column 'pod_ip' does not exist on table 'incidents'" in mock_llm.call_history[1]["prompt"]
    assert "i.service, i.title" in proposal.sql


# ------------------------------------------ 8. Ambiguous / Out-of-Scope Questions


def test_ambiguous_question_returns_empty_proposal_with_rationale(sqlite_settings):
    """Test that ambiguous or out-of-scope questions cleanly return empty SQL with explanation."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql="",
                rationale=(
                    "The operations database does not contain real-time container log streams "
                    "or pod stdout/stderr. Available tables track clusters, nodes, deployments, "
                    "incidents, and SLO breaches."
                ),
                tables=[],
            )
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Stream live container logs for payments-api pod")

    assert proposal.sql == ""
    assert proposal.tables == []
    assert "does not contain real-time container log streams" in proposal.rationale
    assert "does not contain real-time container log streams" in proposal.error


# --------------------------------------------- 9. SQLite Dialect Compatibility


def test_sqlite_ilike_rejected_and_retried(sqlite_settings):
    """Test that ILIKE is rejected when dialect is SQLite, prompting retry with LIKE or LOWER."""
    mock_llm = MockLLM(
        [
            # Attempt 1: Uses PostgreSQL ILIKE
            GeneratedSql(
                sql="SELECT * FROM deployments WHERE service ILIKE 'checkout%'",
                rationale="matching service name case insensitively",
                tables=["deployments"],
            ),
            # Attempt 2: Corrects to LIKE for SQLite
            GeneratedSql(
                sql="SELECT * FROM deployments WHERE service LIKE 'checkout%'",
                rationale="matching service name with SQLite LIKE",
                tables=["deployments"],
            ),
        ]
    )
    generator = SqlGenerator(settings=sqlite_settings, llm=mock_llm)
    proposal = generator.generate("Find deployments starting with checkout")

    assert len(mock_llm.call_history) == 2
    assert "SQLite does not support ILIKE" in mock_llm.call_history[1]["prompt"]
    assert "LIKE 'checkout%'" in proposal.sql


def test_sqlite_schema_prompt_includes_sqlite_rules(sqlite_settings):
    """Verify SQLite prompt includes specific dialect guidance."""
    prompt = get_schema_prompt(sqlite_settings)
    assert "Dialect: SQLite" in prompt
    assert "Dialect Specifics (SQLite):" in prompt
    assert "Do NOT use ILIKE" in prompt


# --------------------------------------- 10. PostgreSQL Dialect-Aware Generation


def test_postgresql_dialect_prompt(pg_settings):
    """Verify PostgreSQL prompt reflects PostgreSQL dialect rules."""
    prompt = get_schema_prompt(pg_settings)
    assert "Dialect: PostgreSQL" in prompt
    assert "Dialect Specifics (PostgreSQL):" in prompt
    assert "ILIKE or LOWER(column)" in prompt


def test_postgresql_allows_ilike_when_configured(pg_settings):
    """Verify ILIKE is permitted when PostgreSQL dialect is configured."""
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql="SELECT * FROM deployments WHERE service ILIKE 'checkout%'",
                rationale="Postgres ILIKE search",
                tables=["deployments"],
            )
        ]
    )
    generator = SqlGenerator(settings=pg_settings, llm=mock_llm)
    proposal = generator.generate("Find deployments matching checkout")

    assert "ILIKE 'checkout%'" in proposal.sql
    assert proposal.read_only is True


# ----------------------------------------- 11. Approval Workflow Preservation


def test_approval_workflow_preserved():
    """Verify that generated proposals are unapproved and read-only by default."""
    settings = Settings(sql_row_limit=50)
    mock_llm = MockLLM(
        [
            GeneratedSql(
                sql="SELECT COUNT(*) FROM incidents",
                rationale="count incidents",
                tables=["incidents"],
            )
        ]
    )
    generator = SqlGenerator(settings=settings, llm=mock_llm)
    proposal = generator.generate("How many incidents occurred?")

    assert proposal.approved is False
    assert proposal.read_only is True
    assert proposal.sql.startswith("SELECT COUNT(*)")


# --------------------------------------------- Schema & Utility Unit Tests


def test_extract_referenced_tables():
    sql = "SELECT i.id, c.name FROM incidents i JOIN clusters c ON c.id = i.cluster_id"
    tables = _extract_referenced_tables(sql)
    assert tables == ["incidents", "clusters"]


def test_extract_table_aliases():
    sql = "SELECT i.id, c.name FROM incidents i JOIN clusters AS c ON c.id = i.cluster_id"
    aliases = _extract_table_aliases(sql, {"incidents", "clusters"})
    assert aliases == {"i": "incidents", "c": "clusters"}


def test_check_column_references_valid():
    sql = "SELECT i.severity, c.name FROM incidents i JOIN clusters c ON c.id = i.cluster_id"
    known = {"incidents", "clusters"}
    cols = {
        "incidents": {"id", "severity", "cluster_id"},
        "clusters": {"id", "name"},
    }
    err = _check_column_references(sql, known, cols)
    assert err is None


def test_check_column_references_invalid():
    sql = "SELECT i.nonexistent_metric FROM incidents i"
    known = {"incidents"}
    cols = {"incidents": {"id", "severity"}}
    err = _check_column_references(sql, known, cols)
    assert err is not None
    assert "column 'nonexistent_metric' does not exist on table 'incidents'" in err


def test_check_dialect_compatibility():
    assert _check_dialect_compatibility("SELECT * FROM t WHERE name ILIKE 'a%'", "SQLite") is not None
    assert _check_dialect_compatibility("SELECT * FROM t WHERE name LIKE 'a%'", "SQLite") is None
    assert _check_dialect_compatibility("SELECT * FROM t WHERE name ILIKE 'a%'", "PostgreSQL") is None

