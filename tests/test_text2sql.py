import pytest

from advanced_rag.config import Settings
from advanced_rag.text2sql.executor import SqlRejected, normalize, validate


@pytest.fixture
def settings() -> Settings:
    return Settings(sql_row_limit=200)


# ---------------------------------------------------------------- normalize


def test_normalize_strips_fences_and_semicolon():
    assert normalize("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert normalize("  SELECT 1 ;  ") == "SELECT 1"


# ----------------------------------------------------------------- validate


def test_select_is_accepted_and_limited(settings):
    assert validate("SELECT * FROM incidents", settings) == "SELECT * FROM incidents LIMIT 200"


def test_cte_is_accepted(settings):
    sql = "WITH x AS (SELECT 1 AS a) SELECT a FROM x"
    assert validate(sql, settings).startswith("WITH x AS")


def test_existing_limit_is_preserved(settings):
    assert validate("SELECT 1 LIMIT 10", settings) == "SELECT 1 LIMIT 10"


def test_oversized_limit_is_clamped(settings):
    assert validate("SELECT 1 LIMIT 50000", settings) == "SELECT 1 LIMIT 200"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM incidents",
        "UPDATE clusters SET node_count = 0",
        "INSERT INTO nodes VALUES (1)",
        "DROP TABLE clusters",
        "TRUNCATE incidents",
        "ALTER TABLE nodes ADD COLUMN x INT",
        "GRANT ALL ON incidents TO public",
    ],
)
def test_write_statements_rejected(settings, sql):
    with pytest.raises(SqlRejected):
        validate(sql, settings)


def test_stacked_statements_rejected(settings):
    with pytest.raises(SqlRejected, match="multiple statements"):
        validate("SELECT 1; DELETE FROM nodes", settings)


def test_comment_smuggling_rejected(settings):
    with pytest.raises(SqlRejected):
        validate("SELECT * FROM nodes -- WHERE 1=1", settings)


def test_empty_query_rejected(settings):
    with pytest.raises(SqlRejected, match="empty"):
        validate("   ", settings)


def test_non_select_rejected(settings):
    with pytest.raises(SqlRejected, match="only SELECT"):
        validate("EXPLAIN SELECT 1", settings)


# ------------------------------------------------------ database round trip


def test_seed_and_query_sqlite(tmp_path, monkeypatch):
    """End-to-end against a throwaway SQLite file - no Postgres needed."""
    import advanced_rag.text2sql.database as database
    from advanced_rag.text2sql import executor

    settings = Settings(postgres_dsn=None, sqlite_path=tmp_path / "ops.db")
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr("advanced_rag.config.get_settings", lambda: settings)

    rows = database.seed_database(settings)
    assert rows > 300

    # Every table the schema prompt advertises must actually have data, or the
    # generator will write valid SQL that returns nothing.
    for table in ("clusters", "nodes", "deployments", "incidents", "slo_breaches"):
        count = executor.execute(f"SELECT COUNT(*) AS n FROM {table}", settings).rows[0][0]
        assert count > 0, f"{table} was not seeded"

    result = executor.execute("SELECT COUNT(*) AS n FROM clusters", settings)
    assert result.columns == ["n"]
    assert result.rows[0][0] == 5

    result = executor.execute(
        "SELECT environment, COUNT(*) AS n FROM clusters GROUP BY environment", settings
    )
    assert dict(result.rows)["production"] == 3

    rendered = executor.render_rows(result)
    assert "environment" in rendered and "production" in rendered


def test_execute_refuses_writes(tmp_path, monkeypatch):
    import advanced_rag.text2sql.database as database
    from advanced_rag.text2sql import executor

    settings = Settings(postgres_dsn=None, sqlite_path=tmp_path / "ops.db")
    monkeypatch.setattr(database, "_engine", None)
    database.seed_database(settings)

    with pytest.raises(SqlRejected):
        executor.execute("DELETE FROM clusters", settings)

    # The table is still intact.
    assert executor.execute("SELECT COUNT(*) AS n FROM clusters", settings).rows[0][0] == 5
