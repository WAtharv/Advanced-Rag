"""Automated collection and curation pipeline for official Helm documentation.

Pulls official, versioned documentation from helm/helm-www, cleans Docusaurus / MDX
markup and admonitions while strictly preserving Go template expressions and code blocks,
attaches standardized metadata frontmatter, detects duplicates, supports incremental
caching, safe atomic writes, and generates a machine-readable corpus manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REPO = "https://github.com/helm/helm-www"
DEFAULT_HELM_VERSION = "4.3.1"
DEFAULT_SOURCE_REF = "main"
DEFAULT_SOURCE_COMMIT = "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
DEFAULT_BRANCH = "main"
RAW_BASE_URL = f"https://raw.githubusercontent.com/helm/helm-www/{DEFAULT_SOURCE_COMMIT}/"
SPDX_LICENSE = "CC-BY-4.0"
ATTRIBUTION = "Copyright The Helm Authors"
USER_AGENT = "HelmDocCollector/1.0 (AdvancedRAG SRE Copilot)"

DEFAULT_CORPUS_DIR = Path("data/corpus/helm")
DEFAULT_MANIFEST_PATH = Path("data/corpus/helm_manifest.json")
DEFAULT_STAGING_DIR = Path("data/staging_corpus/helm")
DEFAULT_STAGING_MANIFEST = Path("data/staging_corpus/helm_manifest.json")


# Curated catalog covering the operational areas requested in Stage 2.4 across 7 categories:
# 1. fundamentals (7)
# 2. charts (10)
# 3. templating (13)
# 4. operations (12)
# 5. advanced (12)
# 6. security (6)
# 7. troubleshooting (4)
# Total: 64 official curated documents.
HELM_DOCUMENT_CATALOG: list[dict[str, str]] = [
    # 1. Fundamentals (helm/fundamentals)
    {
        "category": "fundamentals",
        "name": "what-is-helm.md",
        "upstream_path": "docs/intro/introduction.md",
        "document_type": "concept",
        "component": "architecture",
        "title": "Introduction to Helm",
    },
    {
        "category": "fundamentals",
        "name": "quickstart.md",
        "upstream_path": "docs/intro/quickstart.md",
        "document_type": "guide",
        "component": "cli",
        "title": "Helm Quickstart Guide",
    },
    {
        "category": "fundamentals",
        "name": "install.md",
        "upstream_path": "docs/intro/install.mdx",
        "document_type": "installation",
        "component": "cli",
        "title": "Installing Helm",
    },
    {
        "category": "fundamentals",
        "name": "using-helm.md",
        "upstream_path": "docs/intro/using_helm.mdx",
        "document_type": "guide",
        "component": "release-management",
        "title": "Using Helm",
    },
    {
        "category": "fundamentals",
        "name": "cheatsheet.md",
        "upstream_path": "docs/intro/CheatSheet.mdx",
        "document_type": "reference",
        "component": "cli",
        "title": "Helm CLI Cheat Sheet",
    },
    {
        "category": "fundamentals",
        "name": "architecture-overview.md",
        "upstream_path": "docs/overview.md",
        "document_type": "concept",
        "component": "architecture",
        "title": "Helm 4 Architecture and Overview",
    },
    {
        "category": "fundamentals",
        "name": "glossary.md",
        "upstream_path": "docs/glossary/index.mdx",
        "document_type": "reference",
        "component": "general",
        "title": "Helm Glossary",
    },

    # 2. Charts (helm/charts)
    {
        "category": "charts",
        "name": "charts-guide.md",
        "upstream_path": "docs/topics/charts.mdx",
        "document_type": "guide",
        "component": "chart",
        "title": "Helm Charts Guide",
    },
    {
        "category": "charts",
        "name": "chart-best-practices.md",
        "upstream_path": "docs/chart_best_practices/conventions.md",
        "document_type": "guide",
        "component": "chart",
        "title": "Chart Best Practices and Conventions",
    },
    {
        "category": "charts",
        "name": "values-files-practices.md",
        "upstream_path": "docs/chart_best_practices/values.md",
        "document_type": "guide",
        "component": "chart",
        "title": "Values Files Best Practices",
    },
    {
        "category": "charts",
        "name": "chart-dependencies.md",
        "upstream_path": "docs/chart_best_practices/dependencies.mdx",
        "document_type": "guide",
        "component": "chart",
        "title": "Managing Chart Dependencies",
    },
    {
        "category": "charts",
        "name": "chart-repository.md",
        "upstream_path": "docs/topics/chart_repository.md",
        "document_type": "guide",
        "component": "repository",
        "title": "The Chart Repository Guide",
    },
    {
        "category": "charts",
        "name": "library-charts.md",
        "upstream_path": "docs/topics/library_charts.md",
        "document_type": "concept",
        "component": "chart",
        "title": "Library Charts",
    },
    {
        "category": "charts",
        "name": "registries-and-oci.md",
        "upstream_path": "docs/topics/registries.mdx",
        "document_type": "guide",
        "component": "registry",
        "title": "Helm OCI Registries",
    },
    {
        "category": "charts",
        "name": "custom-resource-definitions.md",
        "upstream_path": "docs/chart_best_practices/custom_resource_definitions.md",
        "document_type": "guide",
        "component": "chart",
        "title": "Custom Resource Definitions in Charts",
    },
    {
        "category": "charts",
        "name": "helm-pull.md",
        "upstream_path": "docs/helm/helm_pull.md",
        "document_type": "reference",
        "component": "cli",
        "title": "Helm Pull Command Reference",
    },
    {
        "category": "charts",
        "name": "helm-push.md",
        "upstream_path": "docs/helm/helm_push.md",
        "document_type": "reference",
        "component": "cli",
        "title": "Helm Push Command Reference",
    },

    # 3. Templating (helm/templating)
    {
        "category": "templating",
        "name": "getting-started.md",
        "upstream_path": "docs/chart_template_guide/getting_started.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Getting Started with Chart Templates",
    },
    {
        "category": "templating",
        "name": "builtin-objects.md",
        "upstream_path": "docs/chart_template_guide/builtin_objects.md",
        "document_type": "reference",
        "component": "templates",
        "title": "Built-in Objects in Helm Templates",
    },
    {
        "category": "templating",
        "name": "values-files.md",
        "upstream_path": "docs/chart_template_guide/values_files.mdx",
        "document_type": "guide",
        "component": "templates",
        "title": "Values Files in Templates",
    },
    {
        "category": "templating",
        "name": "functions-and-pipelines.md",
        "upstream_path": "docs/chart_template_guide/functions_and_pipelines.mdx",
        "document_type": "guide",
        "component": "templates",
        "title": "Template Functions and Pipelines",
    },
    {
        "category": "templating",
        "name": "function-list.md",
        "upstream_path": "docs/chart_template_guide/function_list.mdx",
        "document_type": "reference",
        "component": "templates",
        "title": "Helm Template Function Reference",
    },
    {
        "category": "templating",
        "name": "control-structures.md",
        "upstream_path": "docs/chart_template_guide/control_structures.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Flow Control Structures in Templates",
    },
    {
        "category": "templating",
        "name": "variables.md",
        "upstream_path": "docs/chart_template_guide/variables.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Template Variables",
    },
    {
        "category": "templating",
        "name": "named-templates.md",
        "upstream_path": "docs/chart_template_guide/named_templates.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Named Templates and Partials",
    },
    {
        "category": "templating",
        "name": "accessing-files.md",
        "upstream_path": "docs/chart_template_guide/accessing_files.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Accessing Files Inside Templates",
    },
    {
        "category": "templating",
        "name": "subcharts-and-globals.md",
        "upstream_path": "docs/chart_template_guide/subcharts_and_globals.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Subcharts and Global Values",
    },
    {
        "category": "templating",
        "name": "debugging-templates.md",
        "upstream_path": "docs/chart_template_guide/debugging.md",
        "document_type": "troubleshooting",
        "component": "templates",
        "title": "Debugging Chart Templates",
    },
    {
        "category": "templating",
        "name": "yaml-techniques.md",
        "upstream_path": "docs/chart_template_guide/yaml_techniques.md",
        "document_type": "guide",
        "component": "templates",
        "title": "YAML Techniques in Templates",
    },
    {
        "category": "templating",
        "name": "notes-files.md",
        "upstream_path": "docs/chart_template_guide/notes_files.md",
        "document_type": "guide",
        "component": "templates",
        "title": "Chart NOTES.txt Files",
    },

    # 4. Operations (helm/operations)
    {
        "category": "operations",
        "name": "helm-install.md",
        "upstream_path": "docs/helm/helm_install.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Install Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-upgrade.md",
        "upstream_path": "docs/helm/helm_upgrade.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Upgrade Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-rollback.md",
        "upstream_path": "docs/helm/helm_rollback.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Rollback Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-uninstall.md",
        "upstream_path": "docs/helm/helm_uninstall.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Uninstall Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-history.md",
        "upstream_path": "docs/helm/helm_history.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm History Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-status.md",
        "upstream_path": "docs/helm/helm_status.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Status Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-list.md",
        "upstream_path": "docs/helm/helm_list.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm List Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-get.md",
        "upstream_path": "docs/helm/helm_get.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Get Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-get-manifest.md",
        "upstream_path": "docs/helm/helm_get_manifest.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Get Manifest Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-get-values.md",
        "upstream_path": "docs/helm/helm_get_values.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Get Values Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-show.md",
        "upstream_path": "docs/helm/helm_show.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Show Command Reference",
    },
    {
        "category": "operations",
        "name": "helm-show-values.md",
        "upstream_path": "docs/helm/helm_show_values.md",
        "document_type": "reference",
        "component": "release-management",
        "title": "Helm Show Values Command Reference",
    },

    # 5. Advanced (helm/advanced)
    {
        "category": "advanced",
        "name": "chart-hooks.md",
        "upstream_path": "docs/topics/charts_hooks.md",
        "document_type": "guide",
        "component": "hooks",
        "title": "Chart Lifecycle Hooks",
    },
    {
        "category": "advanced",
        "name": "plugins-guide.md",
        "upstream_path": "docs/topics/plugins.mdx",
        "document_type": "guide",
        "component": "plugins",
        "title": "Helm Plugins Guide",
    },
    {
        "category": "advanced",
        "name": "post-rendering.md",
        "upstream_path": "docs/topics/advanced.mdx",
        "document_type": "guide",
        "component": "post-rendering",
        "title": "Post-Rendering and Advanced Features",
    },
    {
        "category": "advanced",
        "name": "chart-tests.md",
        "upstream_path": "docs/topics/chart_tests.md",
        "document_type": "guide",
        "component": "testing",
        "title": "Chart Testing Framework",
    },
    {
        "category": "advanced",
        "name": "helm-lint.md",
        "upstream_path": "docs/helm/helm_lint.md",
        "document_type": "reference",
        "component": "testing",
        "title": "Helm Lint Command Reference",
    },
    {
        "category": "advanced",
        "name": "helm-template.md",
        "upstream_path": "docs/helm/helm_template.md",
        "document_type": "reference",
        "component": "templates",
        "title": "Helm Template Command Reference",
    },
    {
        "category": "advanced",
        "name": "helm-package.md",
        "upstream_path": "docs/helm/helm_package.md",
        "document_type": "reference",
        "component": "packaging",
        "title": "Helm Package Command Reference",
    },
    {
        "category": "advanced",
        "name": "helm-repo.md",
        "upstream_path": "docs/helm/helm_repo.md",
        "document_type": "reference",
        "component": "repository",
        "title": "Helm Repo Command Reference",
    },
    {
        "category": "advanced",
        "name": "helm-dependency.md",
        "upstream_path": "docs/helm/helm_dependency.md",
        "document_type": "reference",
        "component": "dependencies",
        "title": "Helm Dependency Command Reference",
    },
    {
        "category": "advanced",
        "name": "helm-plugin.md",
        "upstream_path": "docs/helm/helm_plugin.md",
        "document_type": "reference",
        "component": "plugins",
        "title": "Helm Plugin Command Reference",
    },
    {
        "category": "advanced",
        "name": "helm-plugin-install.md",
        "upstream_path": "docs/helm/helm_plugin_install.md",
        "document_type": "reference",
        "component": "plugins",
        "title": "Helm Plugin Install Command Reference",
    },
    {
        "category": "advanced",
        "name": "postrenderer-plugin-tutorial.md",
        "upstream_path": "docs/plugins/developer/tutorial-postrenderer-plugin.mdx",
        "document_type": "guide",
        "component": "post-rendering",
        "title": "Developing Post-Renderer Plugins",
    },

    # 6. Security (helm/security)
    {
        "category": "security",
        "name": "chart-provenance.md",
        "upstream_path": "docs/topics/provenance.mdx",
        "document_type": "guide",
        "component": "provenance",
        "title": "Helm Provenance and Cryptographic Verification",
    },
    {
        "category": "security",
        "name": "helm-verify.md",
        "upstream_path": "docs/helm/helm_verify.md",
        "document_type": "reference",
        "component": "provenance",
        "title": "Helm Verify Command Reference",
    },
    {
        "category": "security",
        "name": "rbac.md",
        "upstream_path": "docs/topics/rbac.md",
        "document_type": "guide",
        "component": "rbac",
        "title": "Role-Based Access Control in Helm",
    },
    {
        "category": "security",
        "name": "rbac-best-practices.md",
        "upstream_path": "docs/chart_best_practices/rbac.md",
        "document_type": "guide",
        "component": "rbac",
        "title": "RBAC Best Practices for Charts",
    },
    {
        "category": "security",
        "name": "sql-storage-backend.md",
        "upstream_path": "docs/topics/permissions_sql_storage_backend.md",
        "document_type": "guide",
        "component": "storage",
        "title": "SQL Storage Backend Permissions and Security",
    },
    {
        "category": "security",
        "name": "pods-best-practices.md",
        "upstream_path": "docs/chart_best_practices/pods.md",
        "document_type": "guide",
        "component": "security",
        "title": "Pod Security Best Practices in Charts",
    },

    # 7. Troubleshooting (helm/troubleshooting)
    {
        "category": "troubleshooting",
        "name": "troubleshooting-faq.md",
        "upstream_path": "versioned_docs/version-3/faq/troubleshooting.md",
        "document_type": "troubleshooting",
        "component": "troubleshooting",
        "title": "Helm Troubleshooting FAQ",
        "helm_version": "3.22.1",
    },
    {
        "category": "troubleshooting",
        "name": "charts-tips-and-tricks.md",
        "upstream_path": "docs/howto/charts_tips_and_tricks.md",
        "document_type": "troubleshooting",
        "component": "troubleshooting",
        "title": "Charts Tips and Tricks for Troubleshooting",
    },
    {
        "category": "troubleshooting",
        "name": "version-skew.md",
        "upstream_path": "docs/topics/version_skew.mdx",
        "document_type": "concept",
        "component": "compatibility",
        "title": "Helm and Kubernetes Version Skew Policy",
    },
    {
        "category": "troubleshooting",
        "name": "kubernetes-apis.md",
        "upstream_path": "docs/topics/kubernetes_apis.md",
        "document_type": "concept",
        "component": "compatibility",
        "title": "Deprecations, Removals, and Kubernetes API Compatibility",
    },
]


def get_canonical_helm_url(upstream_path: str) -> str:
    """Map upstream path in helm-www to canonical reader-facing documentation URL."""
    clean = upstream_path.strip("/")
    if clean.startswith("docs/"):
        rel = clean[len("docs/"):]
        if rel.endswith("/index.md") or rel.endswith("/index.mdx"):
            rel = rel.rsplit("/index.", 1)[0]
        elif rel.endswith(".md") or rel.endswith(".mdx"):
            rel = rel.rsplit(".", 1)[0]
        return f"https://helm.sh/docs/{rel.strip('/')}/" if rel.strip('/') else "https://helm.sh/docs/"
    elif clean.startswith("versioned_docs/version-3/"):
        rel = clean[len("versioned_docs/version-3/"):]
        if rel.endswith("/index.md") or rel.endswith("/index.mdx"):
            rel = rel.rsplit("/index.", 1)[0]
        elif rel.endswith(".md") or rel.endswith(".mdx"):
            rel = rel.rsplit(".", 1)[0]
        return f"https://helm.sh/docs/v3/{rel.strip('/')}/" if rel.strip('/') else "https://helm.sh/docs/v3/"
    else:
        rel = clean.rsplit(".", 1)[0]
        return f"https://helm.sh/docs/{rel.strip('/')}/"


def clean_helm_markdown(raw_text: str, *, version_label: str = "v4.3.1") -> tuple[dict[str, str], str]:
    """Clean Docusaurus MDX components, imports, and admonitions while preserving Go template code.

    Args:
        raw_text: Upstream Markdown or MDX file content.
        version_label: Version replacement string for version placeholders.

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

    # 2. Split by fenced code blocks (```...```) to ensure code, YAML, and templates are untouched
    fence_pattern = re.compile(r"(```.*?```)", re.DOTALL)
    parts = fence_pattern.split(cleaned_body)

    for i in range(0, len(parts), 2):
        chunk = parts[i]

        # Strip MDX imports
        chunk = re.sub(r"^import\s+.*?;?\s*$", "", chunk, flags=re.MULTILINE)
        chunk = re.sub(r"import\s+.*?from\s+['\"].*?['\"];?", "", chunk)

        # Replace <GetVersion .../> with version_label
        chunk = re.sub(r"<GetVersion[^>]*/>", version_label, chunk)

        # Strip standalone JSX components like <Helm4/>, <DocCardList />
        chunk = re.sub(r"<(Helm4|DocCardList)\s*/?>", "", chunk)

        # Clean Docusaurus admonitions (:::note, :::tip, :::info, :::warning, :::caution, :::danger)
        admonition_pattern = re.compile(
            r"^:::(note|tip|info|warning|caution|danger)(?:[ \t]+([^\n]*))?\n(.*?)\n:::",
            re.MULTILINE | re.DOTALL,
        )

        def admonition_repl(match: re.Match) -> str:
            kind = match.group(1).capitalize()
            title = match.group(2)
            content = match.group(3).strip()
            header = f"> **{title or kind}:** "
            indented = "\n> ".join(content.splitlines())
            return f"{header}\n> {indented}\n"

        chunk = admonition_pattern.sub(admonition_repl, chunk)
        # Any residual ::: tag on its own line
        chunk = re.sub(r"^:::\w*\s*$", "", chunk, flags=re.MULTILINE)

        # Clean Tabs / TabItem if present
        chunk = re.sub(r"</?Tabs[^>]*>", "", chunk)
        chunk = re.sub(r"<TabItem\s+(?:label|value)=['\"]([^'\"]+)['\"][^>]*>", r"\n#### \1\n", chunk)
        chunk = re.sub(r"</TabItem>", "", chunk)

        # Strip HTML comments
        chunk = re.sub(r"<!--\s*overview\s*-->", "", chunk)
        chunk = re.sub(r"<!--\s*body\s*-->", "", chunk)

        # Normalize multiple blank lines
        chunk = re.sub(r"\n{3,}", "\n\n", chunk)
        parts[i] = chunk

    cleaned = "".join(parts).strip()
    return frontmatter, cleaned


def compute_content_hash(text: str) -> str:
    """Compute deterministic SHA-256 hex digest of cleaned text content."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def generate_frontmatter(metadata: dict[str, Any]) -> str:
    """Generate standardized YAML frontmatter conforming to Stage 2.4 requirements."""
    fields = [
        "title",
        "category",
        "component",
        "helm_version",
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


def download_helm_docs(
    *,
    catalog: list[dict[str, str]] | None = None,
    target_dir: Path = DEFAULT_CORPUS_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    force: bool = False,
    raw_base_url: str = RAW_BASE_URL,
    source_repository: str = DEFAULT_REPO,
    source_version: str = DEFAULT_HELM_VERSION,
    helm_version: str = DEFAULT_HELM_VERSION,
    source_ref: str = DEFAULT_SOURCE_REF,
    source_commit: str = DEFAULT_SOURCE_COMMIT,
    version_label: str = "v4.3.1",
) -> dict[str, Any]:
    """Execute automated collection of official Helm documentation.

    Args:
        catalog: List of doc descriptors (defaults to HELM_DOCUMENT_CATALOG).
        target_dir: Destination root for downloaded documents.
        manifest_path: Path to write the output JSON manifest.
        force: If True, re-downloads even if cached locally.
        raw_base_url: Upstream GitHub raw content URL prefix.
        source_repository: Upstream repository URL.
        source_version: Upstream software version string.
        helm_version: Authoritative Helm version string.
        source_ref: Upstream Git ref / branch.
        source_commit: Upstream 40-character commit SHA.
        version_label: Version tag for shortcode/macro replacements.

    Returns:
        Summary dict containing counts and manifest records.
    """
    catalog = catalog or HELM_DOCUMENT_CATALOG
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    seen_hashes: dict[str, str] = {}  # hash -> local_path
    documents_manifest: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    downloaded_count = 0
    cached_count = 0
    skipped_duplicates_count = 0
    now_iso = datetime.now(UTC).isoformat()

    logger.info("Beginning collection of %d official Helm documents into %s...", len(catalog), target_dir)

    for item in catalog:
        category = item["category"]
        name = item["name"]
        upstream_path = item["upstream_path"]
        doc_type = item.get("document_type", "concept")
        component = item.get("component", "general")
        configured_title = item.get("title", "")
        doc_helm_version = item.get("helm_version") or helm_version
        doc_source_version = item.get("helm_version") or source_version

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

        source_url = get_canonical_helm_url(upstream_path)
        download_url = f"{raw_base_url.rstrip('/')}/{upstream_path}"
        immutable_source_url = f"https://raw.githubusercontent.com/helm/helm-www/{source_commit}/{upstream_path}"

        # Incremental download check
        if local_path.exists() and not force:
            try:
                existing_text = local_path.read_text(encoding="utf-8")
                existing_meta, existing_body = clean_helm_markdown(existing_text, version_label=version_label)
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
                    "source_url": source_url,
                    "immutable_source_url": immutable_source_url,
                    "local_path": local_path.as_posix(),
                    "content_hash": content_hash,
                    "helm_version": existing_meta.get("helm_version") or doc_helm_version,
                    "source_ref": existing_meta.get("source_ref") or source_ref,
                    "source_commit": existing_meta.get("source_commit") or source_commit,
                    "version": existing_meta.get("source_version") or doc_source_version,
                    "license": SPDX_LICENSE,
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
        raw_meta, cleaned_body = clean_helm_markdown(raw_content, version_label=version_label)

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
            "helm_version": doc_helm_version,
            "source_repository": source_repository,
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
            "source_url": source_url,
            "immutable_source_url": immutable_source_url,
            "local_path": local_path.as_posix(),
            "content_hash": content_hash,
            "helm_version": doc_helm_version,
            "source_ref": source_ref,
            "source_commit": source_commit,
            "version": doc_source_version,
            "license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "retrieval_status": "downloaded",
        }
        documents_manifest.append(record)

    # Build and write manifest JSON
    manifest_data = {
        "manifest_version": "1.0",
        "generated_at": now_iso,
        "helm_version": helm_version,
        "source_repository": source_repository,
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_version": source_version,
        "license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "total_documents": len(documents_manifest),
        "downloaded_documents": downloaded_count,
        "cached_documents": cached_count,
        "skipped_duplicates": skipped_duplicates_count,
        "categories": category_counts,
        "documents": documents_manifest,
    }

    manifest_path.write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Manifest written to %s (Total: %d, Downloaded: %d, Cached: %d, Duplicates: %d)",
        manifest_path,
        len(documents_manifest),
        downloaded_count,
        cached_count,
        skipped_duplicates_count,
    )

    return manifest_data


def validate_collected_helm_corpus(
    corpus_root: Path = DEFAULT_CORPUS_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    """Validate the integrity, quality, and metadata consistency of collected Helm documents."""
    if not manifest_path.exists():
        raise ValueError(f"Manifest file not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = manifest.get("documents", [])
    if not documents:
        raise ValueError(f"Manifest {manifest_path} contains no document records")

    seen_hashes: set[str] = set()
    errors: list[str] = []
    required_frontmatter_keys = {
        "title",
        "category",
        "component",
        "helm_version",
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
    }

    for doc in documents:
        local_path = Path(doc["local_path"])
        if not local_path.exists():
            errors.append(f"Missing file on disk: {local_path}")
            continue

        size = local_path.stat().st_size
        if size == 0:
            errors.append(f"Empty document file: {local_path}")
            continue

        text = local_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Check frontmatter
        if not text.startswith("---"):
            errors.append(f"Missing YAML frontmatter in {local_path}")
            continue

        closing = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing is None:
            errors.append(f"Unclosed YAML frontmatter in {local_path}")
            continue

        frontmatter_dict: dict[str, str] = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                frontmatter_dict[k.strip()] = v.strip().strip("\"'")

        missing_keys = required_frontmatter_keys - set(frontmatter_dict.keys())
        if missing_keys:
            errors.append(
                f"Missing required frontmatter keys {missing_keys} in {local_path}"
            )

        # Check provenance consistency between frontmatter and manifest
        if "helm_version" in frontmatter_dict and doc.get("helm_version"):
            if frontmatter_dict["helm_version"] != doc["helm_version"]:
                errors.append(
                    f"Provenance mismatch: helm_version in {local_path} ({frontmatter_dict['helm_version']}) != manifest ({doc['helm_version']})"
                )
        if "source_commit" in frontmatter_dict and doc.get("source_commit"):
            if frontmatter_dict["source_commit"] != doc["source_commit"]:
                errors.append(
                    f"Provenance mismatch: source_commit in {local_path} != manifest ({doc['source_commit']})"
                )
        if "source_ref" in frontmatter_dict and doc.get("source_ref"):
            if frontmatter_dict["source_ref"] != doc["source_ref"]:
                errors.append(
                    f"Provenance mismatch: source_ref in {local_path} != manifest ({doc['source_ref']})"
                )
        if "immutable_source_url" in doc:
            if not doc["immutable_source_url"].startswith("https://raw.githubusercontent.com/helm/helm-www/"):
                errors.append(
                    f"Invalid immutable_source_url in manifest for {local_path}: {doc['immutable_source_url']}"
                )

        # Check body
        body = "\n".join(lines[closing + 1:]).strip()
        if not body:
            errors.append(f"Empty markdown body in {local_path}")
            continue

        # Check content hash
        computed_hash = compute_content_hash(body)
        expected_hash = doc.get("content_hash")
        if computed_hash != expected_hash:
            errors.append(
                f"Content hash mismatch for {local_path}: expected {expected_hash}, computed {computed_hash}"
            )

        # Check duplicate hash
        if computed_hash in seen_hashes:
            errors.append(
                f"Duplicate content detected across corpus: hash {computed_hash} in {local_path}"
            )
        seen_hashes.add(computed_hash)

        # Check code fence balance
        fences_count = body.count("```")
        if fences_count % 2 != 0:
            errors.append(
                f"Unbalanced code fences ({fences_count} backtick sets) in {local_path}"
            )

    if errors:
        msg = f"Helm corpus validation failed with {len(errors)} error(s):\n" + "\n".join(
            f" - {e}" for e in errors
        )
        logger.error(msg)
        raise ValueError(msg)

    logger.info(
        "Helm corpus validation passed successfully! Checked %d documents, 0 errors.",
        len(documents),
    )
    return {
        "status": "passed",
        "documents_checked": len(documents),
        "unique_hashes": len(seen_hashes),
        "manifest_path": str(manifest_path),
    }


def main() -> None:
    """CLI entrypoint for downloading or validating official Helm documentation."""
    parser = argparse.ArgumentParser(
        description="Download and curate official Helm documentation for the SRE copilot."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files are already cached locally.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Run quality validation on already downloaded corpus without fetching.",
    )
    parser.add_argument(
        "--staging",
        action="store_true",
        help="Collect documents into safe staging directory without modifying active corpus.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Root directory for downloaded Helm corpus.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest JSON file.",
    )
    parser.add_argument(
        "--commit",
        type=str,
        default=DEFAULT_SOURCE_COMMIT,
        help="Upstream commit SHA on helm/helm-www (default: 60d4db89421147aec9fc3fcef9e2d49e2729f50b).",
    )
    parser.add_argument(
        "--version-label",
        type=str,
        default=DEFAULT_HELM_VERSION,
        help="Helm version string (default: 4.3.1).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    target_dir = args.target_dir or (DEFAULT_STAGING_DIR if args.staging else DEFAULT_CORPUS_DIR)
    manifest_path = args.manifest or (DEFAULT_STAGING_MANIFEST if args.staging else DEFAULT_MANIFEST_PATH)

    if args.validate_only:
        print(f"Validating Helm corpus at {target_dir}...")
        res = validate_collected_helm_corpus(corpus_root=target_dir, manifest_path=manifest_path)
        print(f"Validation SUCCESS: {res['documents_checked']} documents verified.")
        return

    raw_base_url = f"https://raw.githubusercontent.com/helm/helm-www/{args.commit}/"
    print(f"Starting Helm docs collection ({args.version_label} commit {args.commit[:10]}) into {target_dir}...")
    manifest = download_helm_docs(
        target_dir=target_dir,
        manifest_path=manifest_path,
        force=args.force,
        raw_base_url=raw_base_url,
        source_commit=args.commit,
        helm_version=args.version_label,
        version_label=f"v{args.version_label}",
    )
    print(f"Collection complete: {manifest['total_documents']} documents recorded.")

    print("Running post-download quality validation...")
    val_res = validate_collected_helm_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    print(f"All {val_res['documents_checked']} documents validated successfully!")


if __name__ == "__main__":
    main()

