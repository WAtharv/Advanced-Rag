"""This file creates and manages the Operations Database used by the Text-to-SQL component.
It:
    ✅ Creates tables (schema)
    ✅ Seeds sample data
    ✅ Supports PostgreSQL and SQLite
    ✅ Executes SQL safely
    ✅ Provides metadata to the LLM

## The operations database: schema, deterministic seed data, and safe execution.

Runs on PostgreSQL when POSTGRES_DSN is set and on SQLite otherwise, so Text2SQL
is exercisable without Docker. The generated SQL is dialect-sensitive, which is
why get_schema_prompt() names the dialect explicitly."""

from __future__ import annotations

import logging
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from advanced_rag.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Python 3.12 deprecated sqlite3's implicit date adapter. Registering an explicit
# one keeps DATE columns as ISO strings, which is also what the generated SQL's
# date comparisons expect.

                                                                            # DDL: Convert Python dates into: 2026-08-27; before storing in SQLite.
                                                                            # DDL-  Contains SQL commands to create tables. 
                                                                            # Table 1: clusters
                                                                            # Table 2: nodes
                                                                            # Table 3: deployments
                                                                            # Table 4: incidents
                                                                            # Table 5: slo_breaches
sqlite3.register_adapter(date, lambda value: value.isoformat())                        
DDL = [                                                                                 
    """                                                                                 
    CREATE TABLE IF NOT EXISTS clusters (                                               
        id INTEGER PRIMARY KEY,
        name VARCHAR(64) NOT NULL,
        region VARCHAR(32) NOT NULL,
        environment VARCHAR(16) NOT NULL,
        k8s_version VARCHAR(16) NOT NULL,
        node_count INTEGER NOT NULL,
        created_at DATE NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY,
        cluster_id INTEGER NOT NULL REFERENCES clusters(id),
        name VARCHAR(64) NOT NULL,
        instance_type VARCHAR(32) NOT NULL,
        status VARCHAR(16) NOT NULL,
        cpu_cores INTEGER NOT NULL,
        memory_gb INTEGER NOT NULL,
        last_ready_at DATE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deployments (
        id INTEGER PRIMARY KEY,
        cluster_id INTEGER NOT NULL REFERENCES clusters(id),
        service VARCHAR(64) NOT NULL,
        namespace VARCHAR(64) NOT NULL,
        version VARCHAR(32) NOT NULL,
        deployed_at DATE NOT NULL,
        deployed_by VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        duration_seconds INTEGER NOT NULL,
        rolled_back INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY,
        cluster_id INTEGER NOT NULL REFERENCES clusters(id),
        service VARCHAR(64) NOT NULL,
        severity VARCHAR(8) NOT NULL,
        title VARCHAR(200) NOT NULL,
        root_cause_category VARCHAR(48) NOT NULL,
        started_at DATE NOT NULL,
        resolved_at DATE,
        duration_minutes INTEGER,
        customer_impacting INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS slo_breaches (
        id INTEGER PRIMARY KEY,
        cluster_id INTEGER NOT NULL REFERENCES clusters(id),
        service VARCHAR(64) NOT NULL,
        slo_name VARCHAR(48) NOT NULL,
        target_pct NUMERIC(5,2) NOT NULL,
        actual_pct NUMERIC(5,2) NOT NULL,
        window_start DATE NOT NULL,
        window_end DATE NOT NULL
    )
    """,
]

#: Column comments folded into the schema prompt - the model guesses far less
#: about semantics when the units and enum values are stated.

                                                                # COLUMN_NOTES: Metadata for LLM.
                                                                # Without notes:LLM may guess meanings
                                                                # With notes:LLM generates better SQL
COLUMN_NOTES = {
    "clusters.environment": "one of production, staging, development",
    "nodes.status": "one of Ready, NotReady, SchedulingDisabled",
    "deployments.status": "one of succeeded, failed, in_progress",
    "deployments.rolled_back": "1 if the deploy was rolled back, else 0",
    "incidents.severity": "one of sev1, sev2, sev3 (sev1 is most severe)",
    "incidents.root_cause_category": (
        "one of config_error, resource_exhaustion, bad_deploy, dependency_failure, "
        "network, capacity, human_error"
    ),
    "incidents.customer_impacting": "1 if customers were affected, else 0",
    "incidents.resolved_at": "NULL while the incident is still open",
    "slo_breaches.target_pct": "the SLO target, e.g. 99.90",
    "slo_breaches.actual_pct": "the achieved percentage in the window",
}

SERVICES = [
    "checkout-api", "payments-api", "search-api", "catalog-api",
    "auth-service", "notification-worker", "recommendation-api", "inventory-sync",
]
ROOT_CAUSES = [
    "config_error", "resource_exhaustion", "bad_deploy",
    "dependency_failure", "network", "capacity", "human_error",
]


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)


_engine: Engine | None = None


def get_engine(settings: Settings | None = None) -> Engine:             # Database Setup
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        url = settings.sql_url
        logger.info("Operations database: %s", url.split("@")[-1])
        _engine = create_engine(url, future=True, pool_pre_ping=True)   # Create a database connection.
    return _engine                                                      # POSTGRES_DSN available → PostgreSQL; Otherwise → SQLite

"""
    Config
    ↓
    Database URL
    ↓
    SQLAlchemy Engine           create_engine()
     `` 
    
    In SQLAlchemy, the Engine is the starting point and central gateway for database operations. 
    
    It manages two fundamental low-level components: a Connection Pool (which holds and reuses active database connections) and 
    a Dialect (which translates SQLAlchemy commands into the specific SQL variant required by target databases like PostgreSQL, MySQL, or SQLite).

    The Engine is typically created once as a global factory object at the module scope of your application.
"""
# ------------------------------------------------------------------- seeding


def seed_database(settings: Settings | None = None) -> int:                     # Main database initialization.
    """
        Create tables
        ↓
        Delete old data
        ↓
        Insert clusters
        ↓
        Insert nodes
        ↓
        Insert deployments
        ↓
        Insert incidents
        ↓
        Insert SLO breaches
        ``
    """
    """Create the schema and fill it with deterministic sample data."""
    settings = settings or get_settings()
    engine = get_engine(settings)
    rng = random.Random(20260828)  # fixed seed: the eval set asserts on these numbers

    with engine.begin() as conn:
        for statement in DDL:
            conn.execute(text(statement))
        # Idempotent: a re-seed replaces the sample data rather than duplicating it.
        for table in ("slo_breaches", "incidents", "deployments", "nodes", "clusters"):
            conn.execute(text(f"DELETE FROM {table}"))

        clusters = _clusters()
        _insert(conn, "clusters", clusters)
        total = len(clusters)
        total += _insert(conn, "nodes", _nodes(clusters, rng))
        total += _insert(conn, "deployments", _deployments(clusters, rng))
        total += _insert(conn, "incidents", _incidents(clusters, rng))
        total += _insert(conn, "slo_breaches", _slo_breaches(clusters, rng))

    logger.info("Seeded operations database with %d rows", total)
    return total


def _clusters() -> list[dict[str, Any]]:                                    # Creates cluster records.
    rows = [    
        ("prod-eu-west-1", "eu-west-1", "production", "1.29.6", 48, date(2023, 4, 11)),
        ("prod-us-east-1", "us-east-1", "production", "1.29.6", 64, date(2023, 2, 2)),
        ("prod-ap-south-1", "ap-south-1", "production", "1.28.11", 24, date(2024, 1, 18)),
        ("staging-eu-west-1", "eu-west-1", "staging", "1.30.2", 12, date(2024, 3, 5)),
        ("dev-eu-west-1", "eu-west-1", "development", "1.30.2", 6, date(2024, 6, 21)),
    ]
    return [
        {
            "id": index,
            "name": name,
            "region": region,
            "environment": environment,
            "k8s_version": version,
            "node_count": nodes,
            "created_at": created,
        }
        for index, (name, region, environment, version, nodes, created) in enumerate(rows, 1)
    ]


def _nodes(clusters: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:             # Creates node data
                                                                                                    # For evert cluster: Creates sample nodes.
    shapes = [("m6i.2xlarge", 8, 32), ("m6i.4xlarge", 16, 64), ("c6i.2xlarge", 8, 16)]
    rows: list[dict[str, Any]] = []
    node_id = 1
    for cluster in clusters:
        # Sample a slice of each cluster's fleet rather than all 48-64 nodes.
        for ordinal in range(min(cluster["node_count"], 10)):
            shape, cpu, memory = shapes[ordinal % len(shapes)]
            status = "Ready"
            if cluster["environment"] == "production" and ordinal == 7:
                status = "NotReady"
            elif ordinal == 9:
                status = "SchedulingDisabled"
            rows.append(
                {
                    "id": node_id,
                    "cluster_id": cluster["id"],
                    "name": f"{cluster['name']}-node-{ordinal:02d}",
                    "instance_type": shape,
                    "status": status,
                    "cpu_cores": cpu,
                    "memory_gb": memory,
                    "last_ready_at": date(2026, 8, 27) - timedelta(days=rng.randint(0, 3)),
                }
            )
            node_id += 1
    return rows


def _deployments(clusters: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:       # Creates deployment history; Randomly creates; ex row, failure probability, rollback probability, used for 
    engineers = ["a.patel", "j.novak", "m.okafor", "s.lindqvist", "r.moreau", "ci-bot"]
    rows: list[dict[str, Any]] = []
    deployment_id = 1
    start = date(2026, 5, 1)
    for day_offset in range(120):
        day = start + timedelta(days=day_offset)
        for _ in range(rng.randint(1, 4)):
            cluster = rng.choice(clusters)
            failed = rng.random() < 0.11
            rows.append(
                {
                    "id": deployment_id,
                    "cluster_id": cluster["id"],
                    "service": rng.choice(SERVICES),
                    "namespace": "default" if rng.random() < 0.4 else "commerce",
                    "version": f"v{rng.randint(1, 4)}.{rng.randint(0, 40)}.{rng.randint(0, 9)}",
                    "deployed_at": day,
                    "deployed_by": rng.choice(engineers),
                    "status": "failed" if failed else "succeeded",
                    "duration_seconds": rng.randint(45, 900),
                    "rolled_back": 1 if failed and rng.random() < 0.7 else 0,
                }
            )
            deployment_id += 1
    return rows


def _incidents(clusters: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:            # Creates outage data: Creates incidents; severity; Root causes, Ex, Used for 
    rows: list[dict[str, Any]] = []
    incident_id = 1
    start = date(2026, 5, 1)
    for day_offset in range(0, 120, 3):
        for _ in range(rng.randint(0, 2)):
            cluster = rng.choice(clusters)
            severity = rng.choices(["sev1", "sev2", "sev3"], weights=[1, 3, 6])[0]
            started = start + timedelta(days=day_offset)
            open_still = rng.random() < 0.05
            duration = rng.randint(8, 240) if severity != "sev1" else rng.randint(20, 180)
            category = rng.choice(ROOT_CAUSES)
            rows.append(
                {
                    "id": incident_id,
                    "cluster_id": cluster["id"],
                    "service": rng.choice(SERVICES),
                    "severity": severity,
                    "title": f"{category.replace('_', ' ').title()} affecting traffic",
                    "root_cause_category": category,
                    "started_at": started,
                    "resolved_at": None if open_still else started,
                    "duration_minutes": None if open_still else duration,
                    "customer_impacting": 1 if severity in ("sev1", "sev2") else 0,
                }
            )
            incident_id += 1
    return rows


def _slo_breaches(clusters: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:      # Creates SLO violation records: Monitors, example, used for 
    slos = [("availability", 99.90), ("latency_p99_under_300ms", 99.00), ("error_rate", 99.95)]
    rows: list[dict[str, Any]] = []
    breach_id = 1
    for week in range(17):
        window_start = date(2026, 5, 4) + timedelta(weeks=week)
        for _ in range(rng.randint(0, 3)):
            slo_name, target = rng.choice(slos)
            rows.append(
                {
                    "id": breach_id,
                    "cluster_id": rng.choice(clusters)["id"],
                    "service": rng.choice(SERVICES),
                    "slo_name": slo_name,
                    "target_pct": target,
                    "actual_pct": round(target - rng.uniform(0.05, 1.8), 2),
                    "window_start": window_start,
                    "window_end": window_start + timedelta(days=6),
                }
            )
            breach_id += 1
    return rows


def _insert(conn, table: str, rows: list[dict[str, Any]]) -> int:               # Generic insert helper: converts, into and bulk inserts rows, 
    if not rows:
        return 0
    columns = list(rows[0].keys())
    statement = text(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)})"
    )
    conn.execute(statement, rows)
    return len(rows)                                                           # return numbers of records inserted 


"""
User Question
      ↓
Text2SQL Generator
      ↓
SELECT COUNT(*)
FROM incidents
WHERE severity='sev1'
      ↓
SQL Execute
      ↓
QueryResult
      ↓
LLM Generates Answer
"""

"""
clusters
    │
    ├── nodes
    │
    ├── deployments
    │
    ├── incidents
    │
    └── slo_breaches
    
This file builds a deterministic Kubernetes operations database containing clusters, nodes, deployments, incidents, and SLO breaches, 
which the Text-to-SQL agent queries using AI-generated SQL to answer operational questions.
"""