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
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import create_engine, event, text
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
TABLE_NOTES = {
    "clusters": "Kubernetes clusters managed across regions and environments",
    "nodes": "Cluster worker nodes/VMs, hardware capacity (CPU, memory), and readiness status",
    "deployments": "Service release history, namespaces, versions, pipeline status, and rollback flags",
    "incidents": "Operational incidents, severity levels (sev1-sev3), affected service, root causes, duration, and customer impact",
    "slo_breaches": "Service Level Objective (SLO) breaches, target vs actual percentages over time windows",
}

RELATIONSHIP_NOTES = [
    "nodes.cluster_id -> clusters.id (nodes belong to a cluster)",
    "deployments.cluster_id -> clusters.id (deployments run on a cluster)",
    "incidents.cluster_id -> clusters.id (incidents occur on a cluster)",
    "slo_breaches.cluster_id -> clusters.id (SLO breaches occur on a cluster)",
    "deployments.service, incidents.service, and slo_breaches.service share identical microservice identifiers",
]

COLUMN_NOTES = {
    # clusters
    "clusters.id": "primary key, referenced by child tables as cluster_id",
    "clusters.name": "unique cluster identifier, e.g. prod-us-east-1, staging-eu-west-1",
    "clusters.region": "cloud region, e.g. us-east-1, us-west-2, eu-west-1",
    "clusters.environment": "deployment tier: one of production, staging, development",
    "clusters.k8s_version": "Kubernetes version string, e.g. 1.28.3, 1.29.1",
    "clusters.node_count": "current number of active nodes in the cluster",
    "clusters.created_at": "cluster provisioning date (ISO format YYYY-MM-DD)",
    # nodes
    "nodes.id": "primary key",
    "nodes.cluster_id": "foreign key -> clusters.id",
    "nodes.name": "node hostname or instance name, e.g. prod-us-east-1-worker-01",
    "nodes.instance_type": "cloud compute instance type, e.g. m5.2xlarge, c5.xlarge",
    "nodes.status": "readiness: one of Ready, NotReady, SchedulingDisabled",
    "nodes.cpu_cores": "number of CPU cores / vCPUs on the node",
    "nodes.memory_gb": "RAM capacity in gigabytes",
    "nodes.last_ready_at": "last date the node reported Ready status (ISO format YYYY-MM-DD)",
    # deployments
    "deployments.id": "primary key",
    "deployments.cluster_id": "foreign key -> clusters.id",
    "deployments.service": "microservice name (e.g. checkout-api, payments-api, auth-service)",
    "deployments.namespace": "Kubernetes namespace (e.g. production, staging, default)",
    "deployments.version": "application version or release tag, e.g. v2.4.1",
    "deployments.deployed_at": "deployment date (ISO format YYYY-MM-DD)",
    "deployments.deployed_by": "operator or automation bot that triggered the deploy (e.g. alice, argocd)",
    "deployments.status": "pipeline outcome: one of succeeded, failed, in_progress",
    "deployments.duration_seconds": "pipeline deployment duration in seconds",
    "deployments.rolled_back": "1 if the deploy was rolled back, else 0",
    # incidents
    "incidents.id": "primary key",
    "incidents.cluster_id": "foreign key -> clusters.id",
    "incidents.service": "affected microservice name",
    "incidents.severity": "incident severity: one of sev1, sev2, sev3 (sev1 is most severe)",
    "incidents.title": "summary description of the operational incident",
    "incidents.root_cause_category": (
        "one of config_error, resource_exhaustion, bad_deploy, dependency_failure, "
        "network, capacity, human_error"
    ),
    "incidents.started_at": "incident start date (ISO format YYYY-MM-DD)",
    "incidents.resolved_at": "incident resolution date (ISO format YYYY-MM-DD), NULL while the incident is still open",
    "incidents.duration_minutes": "total outage duration in minutes, NULL while open",
    "incidents.customer_impacting": "1 if customers were affected, else 0",
    # slo_breaches
    "slo_breaches.id": "primary key",
    "slo_breaches.cluster_id": "foreign key -> clusters.id",
    "slo_breaches.service": "microservice that breached SLO",
    "slo_breaches.slo_name": "SLO indicator name, e.g. availability_99_9, latency_p99",
    "slo_breaches.target_pct": "the SLO target percentage, e.g. 99.90",
    "slo_breaches.actual_pct": "the achieved percentage during the window, e.g. 98.45",
    "slo_breaches.window_start": "breach window start date (ISO format YYYY-MM-DD)",
    "slo_breaches.window_end": "breach window end date (ISO format YYYY-MM-DD)",
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
    column_types: dict[str, str] = field(default_factory=dict)
    truncation_reason: str | None = None
    total_rows: int | None = None
    has_more: bool = False
    page: int = 1
    page_size: int = 50
    offset: int = 0
    execution_duration_ms: float = 0.0
    summary: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
            "column_types": self.column_types,
            "total_rows": self.total_rows if self.total_rows is not None else self.row_count,
            "has_more": self.has_more,
            "page": self.page,
            "page_size": self.page_size,
            "offset": self.offset,
            "execution_duration_ms": self.execution_duration_ms,
            "summary": self.summary,
        }


_engine: Engine | None = None
_readonly_engine: Engine | None = None


def get_engine(settings: Settings | None = None) -> Engine:             # Database Setup
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        url = settings.sql_url
        logger.info("Operations database: %s", url.split("@")[-1])
        _engine = create_engine(url, future=True, pool_pre_ping=True)   # Create a database connection.
    return _engine                                                      # POSTGRES_DSN available → PostgreSQL; Otherwise → SQLite


def get_readonly_engine(settings: Settings | None = None) -> Engine:
    """Obtain or initialize the dedicated read-only SQLAlchemy Engine for Text2SQL.

    Enforces database-level read-only access:
    - SQLite: Uses URI `mode=ro` and executes `PRAGMA query_only = ON` on connection.
    - PostgreSQL: Connects with dedicated read-only credentials via POSTGRES_READONLY_DSN,
      and sets `default_transaction_read_only = ON`. Fails closed if missing.
    - Completely separate from the application write engine.
    """
    global _readonly_engine
    if _readonly_engine is not None:
        return _readonly_engine

    settings = settings or get_settings()
    url = settings.sql_readonly_url
    safe_target = url.split("@")[-1] if "@" in url else "sqlite"
    logger.info("Operations read-only database: %s", safe_target)

    connect_args: dict[str, Any] = {}
    if not settings.postgres_dsn:
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if not settings.postgres_dsn:
        @event.listens_for(engine, "connect")
        def _set_sqlite_readonly(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only = ON;")
            cursor.close()
    else:
        @event.listens_for(engine, "connect")
        def _set_postgres_readonly(dbapi_connection, connection_record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET default_transaction_read_only = ON;")

    _readonly_engine = engine
    return _readonly_engine


def reset_readonly_engine() -> None:
    """Dispose and clear the cached read-only engine (for test isolation)."""
    global _readonly_engine
    if _readonly_engine is not None:
        try:
            _readonly_engine.dispose()
        except Exception:
            pass
        _readonly_engine = None

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