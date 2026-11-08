"""Automated collection and curation pipeline for official Prometheus documentation.

Pulls official, versioned documentation from:
- prometheus/docs (main / cc0672badce86173b9afaa95a59904576bcd6d6a)
- prometheus/prometheus (v3.16.0 / e2e28adeee2c5883d000fc82aca6008cfbf7e96d)
- prometheus/alertmanager (v0.34.2 / e8349048265ccc0871c3d0d668b05eacc68baf50)

Strictly preserves PromQL queries, Go template expressions, configuration YAML,
and fenced code blocks. Attaches standardized metadata frontmatter, detects duplicates,
supports incremental caching, safe atomic writes, and generates a machine-readable
corpus manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Upstream repository definitions and pinned immutable references
PROMETHEUS_DOCS_REPO = "https://github.com/prometheus/docs"
PROMETHEUS_DOCS_REF = "main"
PROMETHEUS_DOCS_COMMIT = "cc0672badce86173b9afaa95a59904576bcd6d6a"

PROMETHEUS_SERVER_REPO = "https://github.com/prometheus/prometheus"
PROMETHEUS_SERVER_REF = "v3.16.0"
PROMETHEUS_SERVER_COMMIT = "e2e28adeee2c5883d000fc82aca6008cfbf7e96d"

PROMETHEUS_ALERTMANAGER_REPO = "https://github.com/prometheus/alertmanager"
PROMETHEUS_ALERTMANAGER_REF = "v0.34.2"
PROMETHEUS_ALERTMANAGER_COMMIT = "e8349048265ccc0871c3d0d668b05eacc68baf50"

# Component version defaults
DEFAULT_PROMETHEUS_VERSION = "3.16.0"
DEFAULT_ALERTMANAGER_VERSION = "0.34.2"
DEFAULT_NODE_EXPORTER_VERSION = "1.12.1"
DEFAULT_DOCS_VERSION = "3.16"

SPDX_LICENSE = "Apache-2.0"
ATTRIBUTION = "Copyright The Prometheus Authors"
USER_AGENT = "PrometheusDocCollector/1.0 (AdvancedRAG SRE Copilot)"

DEFAULT_CORPUS_DIR = Path("data/corpus/prometheus")
DEFAULT_MANIFEST_PATH = Path("data/corpus/prometheus_manifest.json")
DEFAULT_STAGING_DIR = Path("data/staging_corpus/prometheus")
DEFAULT_STAGING_MANIFEST = Path("data/staging_corpus/prometheus_manifest.json")

REPO_CONFIGS: dict[str, dict[str, str]] = {
    "docs": {
        "repo": PROMETHEUS_DOCS_REPO,
        "ref": PROMETHEUS_DOCS_REF,
        "commit": PROMETHEUS_DOCS_COMMIT,
        "raw_base": f"https://raw.githubusercontent.com/prometheus/docs/{PROMETHEUS_DOCS_COMMIT}/",
        "slug_prefix": "",
    },
    "prometheus": {
        "repo": PROMETHEUS_SERVER_REPO,
        "ref": PROMETHEUS_SERVER_REF,
        "commit": PROMETHEUS_SERVER_COMMIT,
        "raw_base": f"https://raw.githubusercontent.com/prometheus/prometheus/{PROMETHEUS_SERVER_COMMIT}/",
        "slug_prefix": "prometheus/latest",
    },
    "alertmanager": {
        "repo": PROMETHEUS_ALERTMANAGER_REPO,
        "ref": PROMETHEUS_ALERTMANAGER_REF,
        "commit": PROMETHEUS_ALERTMANAGER_COMMIT,
        "raw_base": f"https://raw.githubusercontent.com/prometheus/alertmanager/{PROMETHEUS_ALERTMANAGER_COMMIT}/",
        "slug_prefix": "alerting/latest",
    },
}

# Curated catalog covering the operational areas requested in Stage 2.5 across 6 categories:
# 1. fundamentals (12)
# 2. promql (11)
# 3. alertmanager (11)
# 4. kubernetes-monitoring (9)
# 5. exporters (11)
# 6. troubleshooting (10)
# Total: 64 official curated documents.
PROMETHEUS_DOCUMENT_CATALOG: list[dict[str, str]] = [
    # 1. Fundamentals (prometheus/fundamentals) - 12 documents
    {
        "category": "fundamentals",
        "name": "overview.md",
        "repo_key": "docs",
        "upstream_path": "docs/introduction/overview.md",
        "document_type": "concept",
        "component": "architecture",
        "title": "Prometheus Overview and Architecture",
    },
    {
        "category": "fundamentals",
        "name": "getting-started.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/getting_started.md",
        "document_type": "guide",
        "component": "server",
        "title": "Getting Started with Prometheus",
    },
    {
        "category": "fundamentals",
        "name": "installation.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/installation.md",
        "document_type": "installation",
        "component": "deployment",
        "title": "Installing Prometheus",
    },
    {
        "category": "fundamentals",
        "name": "data-model.md",
        "repo_key": "docs",
        "upstream_path": "docs/concepts/data_model.md",
        "document_type": "concept",
        "component": "data-model",
        "title": "Prometheus Data Model",
    },
    {
        "category": "fundamentals",
        "name": "metric-types.md",
        "repo_key": "docs",
        "upstream_path": "docs/concepts/metric_types.md",
        "document_type": "concept",
        "component": "metric-types",
        "title": "Prometheus Metric Types",
    },
    {
        "category": "fundamentals",
        "name": "jobs-and-instances.md",
        "repo_key": "docs",
        "upstream_path": "docs/concepts/jobs_instances.md",
        "document_type": "concept",
        "component": "scraping",
        "title": "Jobs and Instances in Prometheus",
    },
    {
        "category": "fundamentals",
        "name": "storage.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/storage.md",
        "document_type": "concept",
        "component": "tsdb",
        "title": "Prometheus TSDB Storage Architecture",
    },
    {
        "category": "fundamentals",
        "name": "prometheus-server-cli.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/command-line/prometheus.md",
        "document_type": "reference",
        "component": "server",
        "title": "Prometheus Server Command-Line Flags",
    },
    {
        "category": "fundamentals",
        "name": "prometheus-agent.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/prometheus_agent.md",
        "document_type": "guide",
        "component": "agent",
        "title": "Prometheus Agent Mode",
    },
    {
        "category": "fundamentals",
        "name": "feature-flags.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/feature_flags.md",
        "document_type": "reference",
        "component": "features",
        "title": "Prometheus Feature Flags",
    },
    {
        "category": "fundamentals",
        "name": "comparison.md",
        "repo_key": "docs",
        "upstream_path": "docs/introduction/comparison.md",
        "document_type": "concept",
        "component": "architecture",
        "title": "Comparison to Alternative Monitoring Systems",
    },
    {
        "category": "fundamentals",
        "name": "glossary.md",
        "repo_key": "docs",
        "upstream_path": "docs/introduction/glossary.md",
        "document_type": "reference",
        "component": "terminology",
        "title": "Prometheus Glossary",
    },

    # 2. PromQL (prometheus/promql) - 11 documents
    {
        "category": "promql",
        "name": "basics.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/querying/basics.md",
        "document_type": "guide",
        "component": "promql",
        "title": "PromQL Querying Basics",
    },
    {
        "category": "promql",
        "name": "operators.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/querying/operators.md",
        "document_type": "reference",
        "component": "promql",
        "title": "PromQL Operators",
    },
    {
        "category": "promql",
        "name": "functions.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/querying/functions.md",
        "document_type": "reference",
        "component": "promql",
        "title": "PromQL Functions Reference",
    },
    {
        "category": "promql",
        "name": "examples.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/querying/examples.md",
        "document_type": "guide",
        "component": "promql",
        "title": "PromQL Query Examples",
    },
    {
        "category": "promql",
        "name": "query-api.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/querying/api.md",
        "document_type": "reference",
        "component": "http-api",
        "title": "Prometheus HTTP Query API",
    },
    {
        "category": "promql",
        "name": "recording-rules.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/recording_rules.md",
        "document_type": "guide",
        "component": "rules",
        "title": "Prometheus Recording Rules Configuration",
    },
    {
        "category": "promql",
        "name": "promtool.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/command-line/promtool.md",
        "document_type": "reference",
        "component": "promtool",
        "title": "Promtool Command-Line Utility",
    },
    {
        "category": "promql",
        "name": "unit-testing-rules.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/unit_testing_rules.md",
        "document_type": "guide",
        "component": "promtool",
        "title": "Unit Testing Rules with Promtool",
    },
    {
        "category": "promql",
        "name": "template-reference.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/template_reference.md",
        "document_type": "reference",
        "component": "templating",
        "title": "Console and Alert Template Reference",
    },
    {
        "category": "promql",
        "name": "template-examples.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/template_examples.md",
        "document_type": "guide",
        "component": "templating",
        "title": "Prometheus Template Examples",
    },
    {
        "category": "promql",
        "name": "remote-read-api.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/querying/remote_read_api.md",
        "document_type": "reference",
        "component": "storage-api",
        "title": "Prometheus Remote Read API",
    },

    # 3. Alertmanager (prometheus/alertmanager) - 11 documents
    {
        "category": "alertmanager",
        "name": "overview.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/overview.md",
        "document_type": "concept",
        "component": "alertmanager",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager Overview",
    },
    {
        "category": "alertmanager",
        "name": "architecture.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/alertmanager.md",
        "document_type": "concept",
        "component": "alertmanager",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager Architecture",
    },
    {
        "category": "alertmanager",
        "name": "configuration.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/configuration.md",
        "document_type": "reference",
        "component": "alertmanager-config",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager Configuration Reference",
    },
    {
        "category": "alertmanager",
        "name": "high-availability.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/high_availability.md",
        "document_type": "guide",
        "component": "high-availability",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager High Availability Clustering",
    },
    {
        "category": "alertmanager",
        "name": "alerting-rules.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/alerting_rules.md",
        "document_type": "guide",
        "component": "alerting-rules",
        "title": "Prometheus Alerting Rules Configuration",
    },
    {
        "category": "alertmanager",
        "name": "notifications.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/notifications.md",
        "document_type": "guide",
        "component": "notifications",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager Notification Routing and Formatting",
    },
    {
        "category": "alertmanager",
        "name": "notification-examples.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/notification_examples.md",
        "document_type": "guide",
        "component": "notifications",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager Notification Template Examples",
    },
    {
        "category": "alertmanager",
        "name": "alerts-api.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/alerts_api.md",
        "document_type": "reference",
        "component": "alertmanager-api",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Alertmanager Alerts HTTP API",
    },
    {
        "category": "alertmanager",
        "name": "alerting-practices.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/alerting.md",
        "document_type": "guide",
        "component": "alerting-practices",
        "title": "Alerting Best Practices",
    },
    {
        "category": "alertmanager",
        "name": "rules-practices.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/rules.md",
        "document_type": "guide",
        "component": "rule-practices",
        "title": "Alerting and Recording Rule Practices",
    },
    {
        "category": "alertmanager",
        "name": "alertmanager-https.md",
        "repo_key": "alertmanager",
        "upstream_path": "docs/https.md",
        "document_type": "guide",
        "component": "security",
        "prometheus_version": DEFAULT_ALERTMANAGER_VERSION,
        "title": "Securing Alertmanager with TLS and Basic Auth",
    },

    # 4. Kubernetes Monitoring (prometheus/kubernetes-monitoring) - 9 documents
    {
        "category": "kubernetes-monitoring",
        "name": "configuration.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/configuration.md",
        "document_type": "reference",
        "component": "configuration",
        "title": "Prometheus Server Configuration and Service Discovery",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "cadvisor.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/cadvisor.md",
        "document_type": "guide",
        "component": "cadvisor",
        "title": "Monitoring Docker and Container Metrics with cAdvisor",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "remote-write-practices.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/remote_write.md",
        "document_type": "guide",
        "component": "remote-write",
        "title": "Remote Write Tuning and Best Practices",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "remote-write-spec.md",
        "repo_key": "docs",
        "upstream_path": "docs/specs/prw/remote_write_spec_2_0.md",
        "document_type": "reference",
        "component": "remote-write",
        "title": "Prometheus Remote Write 2.0 Specification",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "opentelemetry.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/opentelemetry.md",
        "document_type": "guide",
        "component": "opentelemetry",
        "title": "Ingesting OpenTelemetry Metrics into Prometheus",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "naming-conventions.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/naming.md",
        "document_type": "guide",
        "component": "naming",
        "title": "Metric and Label Naming Conventions",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "instrumentation-practices.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/instrumentation.md",
        "document_type": "guide",
        "component": "instrumentation",
        "title": "Application Instrumentation Best Practices",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "file-sd.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/file-sd.md",
        "document_type": "guide",
        "component": "service-discovery",
        "title": "File-Based Service Discovery in Prometheus",
    },
    {
        "category": "kubernetes-monitoring",
        "name": "http-sd.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/http_sd.md",
        "document_type": "reference",
        "component": "service-discovery",
        "title": "HTTP-Based Service Discovery Specification",
    },

    # 5. Exporters (prometheus/exporters) - 11 documents
    {
        "category": "exporters",
        "name": "exporters-overview.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/exporters.md",
        "document_type": "concept",
        "component": "exporters",
        "title": "Prometheus Exporters and Integrations Overview",
    },
    {
        "category": "exporters",
        "name": "node-exporter.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/node-exporter.md",
        "document_type": "guide",
        "component": "node-exporter",
        "prometheus_version": DEFAULT_NODE_EXPORTER_VERSION,
        "title": "Monitoring Linux Host Metrics with Node Exporter",
    },
    {
        "category": "exporters",
        "name": "multi-target-exporter.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/multi-target-exporter.md",
        "document_type": "guide",
        "component": "exporters",
        "title": "The Multi-Target Exporter Pattern",
    },
    {
        "category": "exporters",
        "name": "writing-exporters.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/writing_exporters.md",
        "document_type": "guide",
        "component": "exporters",
        "title": "Writing Custom Prometheus Exporters",
    },
    {
        "category": "exporters",
        "name": "client-libraries.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/clientlibs.md",
        "document_type": "reference",
        "component": "client-libraries",
        "title": "Prometheus Client Libraries Overview",
    },
    {
        "category": "exporters",
        "name": "writing-client-libraries.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/writing_clientlibs.md",
        "document_type": "guide",
        "component": "client-libraries",
        "title": "Guidelines for Writing Client Libraries",
    },
    {
        "category": "exporters",
        "name": "go-application.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/go-application.md",
        "document_type": "guide",
        "component": "client-golang",
        "title": "Instrumenting a Go Application for Prometheus",
    },
    {
        "category": "exporters",
        "name": "pushing-metrics.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/pushing.md",
        "document_type": "guide",
        "component": "pushgateway",
        "title": "Pushing Metrics to Pushgateway",
    },
    {
        "category": "exporters",
        "name": "pushing-practices.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/pushing.md",
        "document_type": "guide",
        "component": "pushgateway",
        "title": "When to Use Pushgateway Best Practices",
    },
    {
        "category": "exporters",
        "name": "exposition-formats.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/exposition_formats.md",
        "document_type": "reference",
        "component": "exposition",
        "title": "Prometheus Exposition Formats and Protocols",
    },
    {
        "category": "exporters",
        "name": "content-negotiation.md",
        "repo_key": "docs",
        "upstream_path": "docs/instrumenting/content_negotiation.md",
        "document_type": "reference",
        "component": "exposition",
        "title": "Prometheus Content Negotiation Mechanism",
    },

    # 6. Troubleshooting (prometheus/troubleshooting) - 10 documents
    {
        "category": "troubleshooting",
        "name": "faq.md",
        "repo_key": "docs",
        "upstream_path": "docs/introduction/faq.md",
        "document_type": "troubleshooting",
        "component": "faq",
        "title": "Prometheus Frequently Asked Questions",
    },
    {
        "category": "troubleshooting",
        "name": "query-log.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/query-log.md",
        "document_type": "troubleshooting",
        "component": "query-log",
        "title": "Prometheus Query Log for Troubleshooting Slow Queries",
    },
    {
        "category": "troubleshooting",
        "name": "histograms-cardinality.md",
        "repo_key": "docs",
        "upstream_path": "docs/practices/histograms.md",
        "document_type": "troubleshooting",
        "component": "cardinality",
        "title": "Histograms, Summaries, and High Cardinality Troubleshooting",
    },
    {
        "category": "troubleshooting",
        "name": "native-histograms.md",
        "repo_key": "docs",
        "upstream_path": "docs/specs/native_histograms.md",
        "document_type": "reference",
        "component": "native-histograms",
        "title": "Native Histograms Specification and Cardinality Controls",
    },
    {
        "category": "troubleshooting",
        "name": "security.md",
        "repo_key": "docs",
        "upstream_path": "docs/operating/security.md",
        "document_type": "guide",
        "component": "security",
        "title": "Prometheus Security Model",
    },
    {
        "category": "troubleshooting",
        "name": "tls-encryption.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/tls-encryption.md",
        "document_type": "guide",
        "component": "security",
        "title": "Securing Prometheus Endpoints with TLS Encryption",
    },
    {
        "category": "troubleshooting",
        "name": "https-configuration.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/configuration/https.md",
        "document_type": "reference",
        "component": "security",
        "title": "Prometheus HTTPS Server Configuration",
    },
    {
        "category": "troubleshooting",
        "name": "federation.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/federation.md",
        "document_type": "guide",
        "component": "federation",
        "title": "Hierarchical and Cross-Service Federation",
    },
    {
        "category": "troubleshooting",
        "name": "migration-v3.md",
        "repo_key": "prometheus",
        "upstream_path": "docs/migration.md",
        "document_type": "troubleshooting",
        "component": "migration",
        "title": "Migrating to Prometheus 3.x and Compatibility",
    },
    {
        "category": "troubleshooting",
        "name": "basic-auth.md",
        "repo_key": "docs",
        "upstream_path": "docs/guides/basic-auth.md",
        "document_type": "guide",
        "component": "security",
        "title": "Securing Prometheus with Basic Authentication",
    },
]


def get_canonical_prometheus_url(repo_key: str, upstream_path: str) -> str:
    """Map upstream repo and path to canonical reader-facing prometheus.io documentation URL."""
    clean = upstream_path.strip("/")
    if clean.startswith("docs/"):
        rel = clean[len("docs/"):]
    else:
        rel = clean

    if rel.endswith("/index.md") or rel.endswith("/index.markdown"):
        rel = rel.rsplit("/index.", 1)[0]
    elif rel.endswith(".md") or rel.endswith(".markdown"):
        rel = rel.rsplit(".", 1)[0]

    rel = rel.strip("/")

    if repo_key == "docs":
        return f"https://prometheus.io/docs/{rel}/" if rel else "https://prometheus.io/docs/"
    elif repo_key == "prometheus":
        return f"https://prometheus.io/docs/prometheus/latest/{rel}/" if rel else "https://prometheus.io/docs/prometheus/latest/"
    elif repo_key == "alertmanager":
        return f"https://prometheus.io/docs/alerting/latest/{rel}/" if rel else "https://prometheus.io/docs/alerting/latest/"
    else:
        return f"https://prometheus.io/docs/{rel}/"


def clean_prometheus_markdown(raw_text: str) -> tuple[dict[str, str], str]:
    """Clean upstream frontmatter and comments while strictly preserving PromQL and templates.

    Args:
        raw_text: Upstream Markdown file content.

    Returns:
        tuple of (extracted_upstream_frontmatter, cleaned_body).
    """
    frontmatter: dict[str, str] = {}
    cleaned_body = raw_text

    # 1. Parse leading frontmatter
    if raw_text.startswith("---"):
        lines = raw_text.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if closing is not None:
            for line in lines[1:closing]:
                if ":" in line:
                    k, _, v = line.partition(":")
                    frontmatter[k.strip()] = v.strip().strip("\"'")
            cleaned_body = "\n".join(lines[closing + 1:])

    # 2. Split by fenced code blocks (```...```) to ensure PromQL queries, YAML,
    # and Go template expressions inside code blocks remain completely untouched.
    fence_pattern = re.compile(r"(```.*?```)", re.DOTALL)
    parts = fence_pattern.split(cleaned_body)

    for i in range(0, len(parts), 2):
        chunk = parts[i]

        # Strip HTML comments (e.g., <!-- ... -->)
        chunk = re.sub(r"<!--.*?-->", "", chunk, flags=re.DOTALL)

        # Normalize multiple blank lines (max 2 consecutive newlines)
        chunk = re.sub(r"\n{3,}", "\n\n", chunk)
        parts[i] = chunk

    cleaned = "".join(parts).strip()
    return frontmatter, cleaned


def compute_content_hash(text: str) -> str:
    """Compute deterministic SHA-256 hex digest of cleaned text content."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def generate_frontmatter(metadata: dict[str, Any]) -> str:
    """Generate standardized YAML frontmatter conforming to Stage 2.5 requirements."""
    fields = [
        "title",
        "category",
        "component",
        "prometheus_version",
        "documentation_version",
        "source_repository",
        "source_ref",
        "source_commit",
        "source_url",
        "source_version",
        "retrieved_at",
        "spdx_license",
        "attribution",
        "content_hash",
        "document_type",
        "doc_type",
    ]
    lines = ["---"]
    for f in fields:
        val = metadata.get(f, "")
        clean_val = str(val).replace('"', '\\"')
        lines.append(f'{f}: "{clean_val}"')
    lines.append("---")
    return "\n".join(lines)


def fetch_with_retries(
    url: str,
    *,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff: float = 2.0,
    timeout: float = 15.0,
) -> str:
    """Download text content with exponential backoff retries on network/HTTP errors."""
    delay = initial_delay
    last_err: Exception | None = None
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
                return raw_bytes.decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 404:
                raise
            logger.warning(
                "HTTP %d on attempt %d/%d for %s; retrying in %.1fs...",
                exc.code,
                attempt,
                max_retries,
                url,
                delay,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            logger.warning(
                "Network error '%s' on attempt %d/%d for %s; retrying in %.1fs...",
                exc,
                attempt,
                max_retries,
                url,
                delay,
            )

        if attempt < max_retries:
            time.sleep(delay)
            delay *= backoff

    raise RuntimeError(
        f"Failed to fetch {url} after {max_retries} attempts: {last_err}"
    ) from last_err


def download_prometheus_docs(
    *,
    catalog: list[dict[str, str]] | None = None,
    target_dir: Path = DEFAULT_CORPUS_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    force: bool = False,
    documentation_version: str = DEFAULT_DOCS_VERSION,
) -> dict[str, Any]:
    """Execute automated collection of official Prometheus documentation.

    Args:
        catalog: List of doc descriptors (defaults to PROMETHEUS_DOCUMENT_CATALOG).
        target_dir: Destination root for downloaded documents.
        manifest_path: Path to write the output JSON manifest.
        force: If True, re-downloads even if cached locally.
        documentation_version: Authoritative docs version label.

    Returns:
        Summary dict containing counts and manifest records.
    """
    catalog = catalog or PROMETHEUS_DOCUMENT_CATALOG
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    seen_hashes: dict[str, str] = {}  # hash -> local_path
    documents_manifest: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    downloaded_count = 0
    cached_count = 0
    skipped_duplicates_count = 0
    now_iso = datetime.now(UTC).isoformat()

    logger.info("Beginning collection of %d official Prometheus documents into %s...", len(catalog), target_dir)

    for item in catalog:
        category = item["category"]
        name = item["name"]
        repo_key = item.get("repo_key", "docs")
        upstream_path = item["upstream_path"]
        doc_type = item.get("document_type", "concept")
        component = item.get("component", "general")
        configured_title = item.get("title", "")

        repo_cfg = REPO_CONFIGS.get(repo_key, REPO_CONFIGS["docs"])
        source_repo = repo_cfg["repo"]
        source_ref = repo_cfg["ref"]
        source_commit = repo_cfg["commit"]
        raw_base_url = repo_cfg["raw_base"]

        doc_prom_version = item.get("prometheus_version") or DEFAULT_PROMETHEUS_VERSION
        doc_source_version = item.get("prometheus_version") or DEFAULT_PROMETHEUS_VERSION

        cat_dir = target_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        local_path = cat_dir / name

        # Path traversal prevention defense-in-depth
        try:
            resolved_local = local_path.resolve()
            resolved_target = target_dir.resolve()
            if not resolved_local.is_relative_to(resolved_target):
                raise ValueError(f"Path traversal detected: {local_path} is outside {target_dir}")
        except Exception:
            pass

        source_url = get_canonical_prometheus_url(repo_key, upstream_path)
        download_url = f"{raw_base_url.rstrip('/')}/{upstream_path}"
        immutable_source_url = download_url

        # Incremental download check
        if local_path.exists() and not force:
            try:
                existing_text = local_path.read_text(encoding="utf-8")
                existing_meta, existing_body = clean_prometheus_markdown(existing_text)
                content_hash = existing_meta.get("content_hash") or compute_content_hash(existing_body)

                if content_hash in seen_hashes:
                    logger.warning(
                        "Duplicate content detected for cached %s (matches %s); skipping.",
                        local_path,
                        seen_hashes[content_hash],
                    )
                    skipped_duplicates_count += 1
                    continue

                seen_hashes[content_hash] = str(local_path)
                cached_count += 1
                category_counts[category] = category_counts.get(category, 0) + 1

                record = {
                    "name": name,
                    "title": existing_meta.get("title") or configured_title,
                    "category": category,
                    "component": existing_meta.get("component") or component,
                    "document_type": existing_meta.get("document_type") or doc_type,
                    "upstream_path": upstream_path,
                    "repo_key": repo_key,
                    "source_url": source_url,
                    "immutable_source_url": immutable_source_url,
                    "local_path": local_path.as_posix(),
                    "content_hash": content_hash,
                    "prometheus_version": existing_meta.get("prometheus_version") or doc_prom_version,
                    "documentation_version": existing_meta.get("documentation_version") or documentation_version,
                    "source_repository": existing_meta.get("source_repository") or source_repo,
                    "source_ref": existing_meta.get("source_ref") or source_ref,
                    "source_commit": existing_meta.get("source_commit") or source_commit,
                    "source_version": existing_meta.get("source_version") or doc_source_version,
                    "spdx_license": SPDX_LICENSE,
                    "attribution": ATTRIBUTION,
                    "retrieval_status": "cached",
                }
                documents_manifest.append(record)
                continue
            except Exception as exc:
                logger.warning("Failed to read cached %s (%s); will re-download.", local_path, exc)

        # Download from upstream
        logger.info("Fetching [%s] %s -> %s", category, upstream_path, local_path)
        raw_content = fetch_with_retries(download_url)
        raw_meta, cleaned_body = clean_prometheus_markdown(raw_content)

        # Determine authoritative title
        title = configured_title or raw_meta.get("title") or name.rsplit(".", 1)[0].replace("-", " ").title()

        # Compute content hash on cleaned markdown body
        content_hash = compute_content_hash(cleaned_body)

        # Duplicate detection across the corpus
        if content_hash in seen_hashes:
            logger.warning(
                "Duplicate content hash %s for %s (already seen in %s); skipping duplicate.",
                content_hash,
                local_path,
                seen_hashes[content_hash],
            )
            skipped_duplicates_count += 1
            continue

        seen_hashes[content_hash] = str(local_path)

        # Build metadata & front matter
        meta = {
            "title": title,
            "category": category,
            "component": component,
            "prometheus_version": doc_prom_version,
            "documentation_version": documentation_version,
            "source_repository": source_repo,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "source_url": source_url,
            "source_version": doc_source_version,
            "retrieved_at": now_iso,
            "spdx_license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "content_hash": content_hash,
            "document_type": doc_type,
            "doc_type": doc_type,
        }
        frontmatter = generate_frontmatter(meta)
        full_text = f"{frontmatter}\n\n{cleaned_body}\n"

        # Write to local file atomically
        local_path.write_text(full_text, encoding="utf-8")
        downloaded_count += 1
        category_counts[category] = category_counts.get(category, 0) + 1

        record = {
            "name": name,
            "title": title,
            "category": category,
            "component": component,
            "document_type": doc_type,
            "upstream_path": upstream_path,
            "repo_key": repo_key,
            "source_url": source_url,
            "immutable_source_url": immutable_source_url,
            "local_path": local_path.as_posix(),
            "content_hash": content_hash,
            "prometheus_version": doc_prom_version,
            "documentation_version": documentation_version,
            "source_repository": source_repo,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "source_version": doc_source_version,
            "spdx_license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "retrieval_status": "downloaded",
        }
        documents_manifest.append(record)

    # Build and write manifest JSON
    manifest_data = {
        "manifest_version": "1.0",
        "generated_at": now_iso,
        "corpus": "prometheus",
        "prometheus_version": DEFAULT_PROMETHEUS_VERSION,
        "documentation_version": documentation_version,
        "sources": {
            "docs": {
                "repository": PROMETHEUS_DOCS_REPO,
                "ref": PROMETHEUS_DOCS_REF,
                "commit": PROMETHEUS_DOCS_COMMIT,
            },
            "prometheus": {
                "repository": PROMETHEUS_SERVER_REPO,
                "ref": PROMETHEUS_SERVER_REF,
                "commit": PROMETHEUS_SERVER_COMMIT,
            },
            "alertmanager": {
                "repository": PROMETHEUS_ALERTMANAGER_REPO,
                "ref": PROMETHEUS_ALERTMANAGER_REF,
                "commit": PROMETHEUS_ALERTMANAGER_COMMIT,
            },
        },
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "total_documents": len(documents_manifest),
        "categories": category_counts,
        "documents": documents_manifest,
    }

    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Prometheus document collection finished: %d total (%d downloaded, %d cached, %d duplicates skipped).",
        len(documents_manifest),
        downloaded_count,
        cached_count,
        skipped_duplicates_count,
    )

    return {
        "total_documents": len(documents_manifest),
        "downloaded_count": downloaded_count,
        "cached_count": cached_count,
        "skipped_duplicates_count": skipped_duplicates_count,
        "category_counts": category_counts,
        "manifest_path": str(manifest_path),
    }


def promote_staged_corpus(
    staging_dir: Path = DEFAULT_STAGING_DIR,
    active_dir: Path = DEFAULT_CORPUS_DIR,
    staging_manifest: Path = DEFAULT_STAGING_MANIFEST,
    active_manifest: Path = DEFAULT_MANIFEST_PATH,
) -> int:
    """Promote staged documents and manifest to active corpus atomically."""
    if not staging_dir.exists():
        raise FileNotFoundError(f"Staging directory {staging_dir} does not exist")

    active_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for staged_file in staging_dir.rglob("*.md"):
        rel = staged_file.relative_to(staging_dir)
        target = active_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_file, target)
        count += 1

    if staging_manifest.exists():
        manifest_data = json.loads(staging_manifest.read_text(encoding="utf-8"))
        # Update local_path references to point to active_dir
        for doc in manifest_data.get("documents", []):
            staged_path = Path(doc["local_path"])
            try:
                rel = staged_path.relative_to(staging_dir)
                doc["local_path"] = (active_dir / rel).as_posix()
            except ValueError:
                pass
        active_manifest.parent.mkdir(parents=True, exist_ok=True)
        active_manifest.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Promoted %d documents from %s to %s", count, staging_dir, active_dir)
    return count


def validate_collected_prometheus_corpus(
    corpus_root: Path = DEFAULT_CORPUS_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Perform rigorous quality and integrity validation of the Prometheus corpus.

    Checks:
    - Manifest file presence and valid JSON schema.
    - Total document count equals expected 64 documents.
    - Every file exists, is non-empty, and has closing frontmatter.
    - Frontmatter metadata includes all required fields and correct license/attribution.
    - Code fences are balanced.
    - Content hash matches SHA-256 of cleaned body.
    - No duplicate content hashes across the corpus.
    - Manifest records accurately match local files and provenance.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"Corpus manifest not found at {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Corrupted manifest JSON at {manifest_path}: {exc}") from exc

    docs_list = manifest.get("documents", [])
    if len(docs_list) != len(PROMETHEUS_DOCUMENT_CATALOG):
        raise ValueError(
            f"Expected {len(PROMETHEUS_DOCUMENT_CATALOG)} documents in manifest, found {len(docs_list)}"
        )

    seen_hashes: dict[str, str] = {}
    verified_files = 0

    for doc_entry in docs_list:
        path = Path(doc_entry["local_path"])
        if not path.exists():
            raise FileNotFoundError(f"Document registered in manifest missing from disk: {path}")

        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError(f"Empty document found at {path}")

        if not raw.startswith("---"):
            raise ValueError(f"Missing YAML frontmatter start in {path}")

        lines = raw.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if closing is None:
            raise ValueError(f"Unclosed frontmatter in {path}")

        fm: dict[str, str] = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("\"'")

        # Validate required metadata keys
        required_keys = [
            "title",
            "category",
            "component",
            "prometheus_version",
            "documentation_version",
            "source_repository",
            "source_ref",
            "source_commit",
            "source_url",
            "source_version",
            "retrieved_at",
            "spdx_license",
            "attribution",
            "content_hash",
            "document_type",
            "doc_type",
        ]
        for rk in required_keys:
            if not fm.get(rk):
                raise ValueError(f"Missing required frontmatter key '{rk}' in {path}")

        if fm["spdx_license"] != SPDX_LICENSE:
            raise ValueError(f"Invalid license '{fm['spdx_license']}' in {path}, expected {SPDX_LICENSE}")

        if fm["attribution"] != ATTRIBUTION:
            raise ValueError(f"Invalid attribution in {path}: {fm['attribution']}")

        body = "\n".join(lines[closing + 1:]).strip()
        computed_hash = compute_content_hash(body)

        if computed_hash != fm["content_hash"]:
            raise ValueError(
                f"Frontmatter content_hash mismatch in {path}: {fm['content_hash']} != computed {computed_hash}"
            )
        if computed_hash != doc_entry["content_hash"]:
            raise ValueError(
                f"Manifest content_hash mismatch for {path}: {doc_entry['content_hash']} != computed {computed_hash}"
            )

        if computed_hash in seen_hashes:
            raise ValueError(
                f"Duplicate content detected between {path} and {seen_hashes[computed_hash]} (hash: {computed_hash})"
            )
        seen_hashes[computed_hash] = str(path)

        # Check code fence balance
        fences = [line for line in lines[closing + 1:] if line.strip().startswith("```")]
        if len(fences) % 2 != 0:
            raise ValueError(f"Unbalanced code fences detected in {path} ({len(fences)} fence markers)")

        # Validate provenance consistency with manifest entry
        for prov_key in ("source_ref", "source_commit", "source_repository", "prometheus_version"):
            if fm[prov_key] != doc_entry.get(prov_key):
                raise ValueError(
                    f"Provenance mismatch for {prov_key} in {path}: frontmatter='{fm[prov_key]}' vs manifest='{doc_entry.get(prov_key)}'"
                )

        verified_files += 1

    return {
        "status": "passed",
        "verified_documents": verified_files,
        "manifest_documents": len(docs_list),
        "unique_content_hashes": len(seen_hashes),
        "corpus_root": str(corpus_root),
        "manifest_path": str(manifest_path),
    }


def main() -> None:
    """CLI entrypoint for Prometheus doc downloader."""
    parser = argparse.ArgumentParser(description="Official Prometheus Documentation Collection Pipeline")
    parser.add_argument("--force", action="store_true", help="Re-download all documents even if cached")
    parser.add_argument("--staging", action="store_true", help="Download to staging directory instead of active corpus")
    parser.add_argument("--promote", action="store_true", help="Promote staged corpus to active corpus")
    parser.add_argument("--verify-only", action="store_true", help="Run verification without downloading")
    parser.add_argument("--target-dir", type=Path, default=None, help="Custom target directory")
    parser.add_argument("--manifest-path", type=Path, default=None, help="Custom manifest output path")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.verify_only:
        corpus = args.target_dir or DEFAULT_CORPUS_DIR
        manifest = args.manifest_path or DEFAULT_MANIFEST_PATH
        res = validate_collected_prometheus_corpus(corpus_root=corpus, manifest_path=manifest)
        print(f"Validation passed: {res}")
        return

    if args.promote:
        promoted = promote_staged_corpus()
        print(f"Promoted {promoted} documents to active corpus.")
        return

    target = args.target_dir or (DEFAULT_STAGING_DIR if args.staging else DEFAULT_CORPUS_DIR)
    manifest = args.manifest_path or (DEFAULT_STAGING_MANIFEST if args.staging else DEFAULT_MANIFEST_PATH)

    res = download_prometheus_docs(
        target_dir=target,
        manifest_path=manifest,
        force=args.force,
    )
    print(f"Collection complete: {res}")


if __name__ == "__main__":
    main()

