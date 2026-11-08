"""Unit tests for automated official Prometheus documentation collection pipeline.

Validates:
- Downloader behavior with mocked network responses.
- Incremental download and caching logic.
- Duplicate detection and suppression.
- Metadata extraction, frontmatter formatting, and SPDX license tags.
- Retry behavior and error handling on network/HTTP failures.
- Manifest generation, category distribution, and structure.
- Markdown cleaning: PromQL query preservation, Go template preservation, and fence integrity.
- Quality validation checks (catching empty files, unbalanced fences, provenance mismatch).
- Active corpus provenance schema and immutability verification.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from advanced_rag.ingestion.prometheus_downloader import (
    ATTRIBUTION,
    DEFAULT_ALERTMANAGER_VERSION,
    DEFAULT_CORPUS_DIR,
    DEFAULT_DOCS_VERSION,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROMETHEUS_VERSION,
    PROMETHEUS_ALERTMANAGER_COMMIT,
    PROMETHEUS_ALERTMANAGER_REF,
    PROMETHEUS_ALERTMANAGER_REPO,
    PROMETHEUS_DOCS_COMMIT,
    PROMETHEUS_DOCS_REF,
    PROMETHEUS_DOCS_REPO,
    PROMETHEUS_SERVER_COMMIT,
    PROMETHEUS_SERVER_REF,
    PROMETHEUS_SERVER_REPO,
    SPDX_LICENSE,
    clean_prometheus_markdown,
    compute_content_hash,
    download_prometheus_docs,
    fetch_with_retries,
    generate_frontmatter,
    get_canonical_prometheus_url,
    promote_staged_corpus,
    validate_collected_prometheus_corpus,
)


class MockHTTPResponse:
    """Mock context manager for urllib.request.urlopen."""

    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.status = status

    def read(self) -> bytes:
        return self.content

    def __enter__(self) -> MockHTTPResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


SAMPLE_RAW_PROMETHEUS_MARKDOWN = """---
title: Querying basics
nav_title: Basics
sort_rank: 1
---

<!-- HTML comment to be stripped -->
Prometheus provides a functional query language called PromQL.

When querying Prometheus, instant vectors and range vectors can be used:

`rate(http_requests_total{status=~"5.."}[5m]) > 0.05`

```yaml
# Prometheus alerting rule example:
groups:
- name: example
  rules:
  - alert: HighRequestLatency
    expr: job:request_latency_seconds:mean5m{job="myjob"} > 0.5
    for: 10m
    labels:
      severity: page
    annotations:
      summary: High request latency on {{ $labels.instance }}
      description: The latency is currently {{ $value }}s.
```

In templates, Go template actions like `{{ .GroupLabels.alertname }}` are evaluated.
"""


def test_clean_prometheus_markdown_preserves_promql_and_templates():
    raw_meta, cleaned_body = clean_prometheus_markdown(SAMPLE_RAW_PROMETHEUS_MARKDOWN)

    assert raw_meta["title"] == "Querying basics"
    assert raw_meta["nav_title"] == "Basics"

    # HTML comments stripped
    assert "<!-- HTML comment to be stripped -->" not in cleaned_body

    # PromQL inline query preserved
    assert 'rate(http_requests_total{status=~"5.."}[5m]) > 0.05' in cleaned_body

    # Go templates in YAML code block strictly preserved verbatim
    assert "```yaml" in cleaned_body
    assert "HighRequestLatency" in cleaned_body
    assert "High request latency on {{ $labels.instance }}" in cleaned_body
    assert "The latency is currently {{ $value }}s." in cleaned_body

    # Go templates in prose preserved
    assert "{{ .GroupLabels.alertname }}" in cleaned_body

    # Code fences are balanced
    assert cleaned_body.count("```") == 2


def test_get_canonical_prometheus_url():
    # Docs repository (slugPrefix: "")
    assert (
        get_canonical_prometheus_url("docs", "docs/concepts/data_model.md")
        == "https://prometheus.io/docs/concepts/data_model/"
    )
    assert (
        get_canonical_prometheus_url("docs", "docs/introduction/overview.md")
        == "https://prometheus.io/docs/introduction/overview/"
    )

    # Core server repository (slugPrefix: "prometheus/latest")
    assert (
        get_canonical_prometheus_url("prometheus", "docs/querying/basics.md")
        == "https://prometheus.io/docs/prometheus/latest/querying/basics/"
    )
    assert (
        get_canonical_prometheus_url("prometheus", "docs/configuration/configuration.md")
        == "https://prometheus.io/docs/prometheus/latest/configuration/configuration/"
    )

    # Alertmanager repository (slugPrefix: "alerting/latest")
    assert (
        get_canonical_prometheus_url("alertmanager", "docs/overview.md")
        == "https://prometheus.io/docs/alerting/latest/overview/"
    )
    assert (
        get_canonical_prometheus_url("alertmanager", "docs/configuration.md")
        == "https://prometheus.io/docs/alerting/latest/configuration/"
    )


def test_compute_content_hash():
    t1 = "# PromQL Guide\nQuery series."
    t2 = "# PromQL Guide\nQuery series."
    t3 = "# PromQL Guide\nQuery different series."
    h1 = compute_content_hash(t1)
    h2 = compute_content_hash(t2)
    h3 = compute_content_hash(t3)

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_generate_frontmatter_prometheus_required_fields():
    meta = {
        "title": "PromQL Querying Basics",
        "category": "promql",
        "component": "promql",
        "prometheus_version": DEFAULT_PROMETHEUS_VERSION,
        "documentation_version": DEFAULT_DOCS_VERSION,
        "source_repository": PROMETHEUS_SERVER_REPO,
        "source_ref": PROMETHEUS_SERVER_REF,
        "source_commit": PROMETHEUS_SERVER_COMMIT,
        "source_url": "https://prometheus.io/docs/prometheus/latest/querying/basics/",
        "source_version": DEFAULT_PROMETHEUS_VERSION,
        "retrieved_at": "2026-11-08T05:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": "promhash123",
        "document_type": "guide",
        "doc_type": "guide",
    }
    fm = generate_frontmatter(meta)

    assert fm.startswith("---\n")
    assert fm.endswith("\n---")
    assert 'title: "PromQL Querying Basics"' in fm
    assert 'category: "promql"' in fm
    assert f'prometheus_version: "{DEFAULT_PROMETHEUS_VERSION}"' in fm
    assert f'documentation_version: "{DEFAULT_DOCS_VERSION}"' in fm
    assert f'source_commit: "{PROMETHEUS_SERVER_COMMIT}"' in fm
    assert f'source_ref: "{PROMETHEUS_SERVER_REF}"' in fm
    assert f'source_repository: "{PROMETHEUS_SERVER_REPO}"' in fm
    assert 'source_url: "https://prometheus.io/docs/prometheus/latest/querying/basics/"' in fm
    assert 'spdx_license: "Apache-2.0"' in fm
    assert 'attribution: "Copyright The Prometheus Authors"' in fm
    assert 'content_hash: "promhash123"' in fm


def test_fetch_with_retries_prometheus_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockHTTPResponse(b"# Prometheus Docs Content")
        res = fetch_with_retries("https://raw.githubusercontent.com/prometheus/docs/test.md")
        assert res == "# Prometheus Docs Content"
        mock_urlopen.assert_called_once()


def test_fetch_with_retries_prometheus_recovers_transient():
    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        err_503 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/prometheus/docs/test.md",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = [err_503, MockHTTPResponse(b"# Recovered Content")]
        res = fetch_with_retries(
            "https://raw.githubusercontent.com/prometheus/docs/test.md",
            max_retries=3,
            initial_delay=0.01,
        )
        assert res == "# Recovered Content"
        assert mock_urlopen.call_count == 2


def test_fetch_with_retries_prometheus_404_raises_immediately():
    with patch("urllib.request.urlopen") as mock_urlopen:
        err_404 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/prometheus/docs/test.md",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = err_404
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            fetch_with_retries("https://raw.githubusercontent.com/prometheus/docs/test.md", max_retries=3)
        assert exc_info.value.code == 404
        assert mock_urlopen.call_count == 1


def test_fetch_with_retries_prometheus_exhausts_retries():
    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        err_500 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/prometheus/docs/test.md",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = err_500
        with pytest.raises(RuntimeError) as exc_info:
            fetch_with_retries("https://raw.githubusercontent.com/prometheus/docs/test.md", max_retries=3, initial_delay=0.01)
        assert "Failed to fetch" in str(exc_info.value)
        assert mock_urlopen.call_count == 3


def test_download_prometheus_docs_end_to_end(tmp_path):
    target_dir = tmp_path / "corpus" / "prometheus"
    manifest_path = tmp_path / "prometheus_manifest.json"

    dummy_catalog = [
        {
            "category": "fundamentals",
            "name": "dummy-overview.md",
            "repo_key": "docs",
            "upstream_path": "docs/introduction/overview.md",
            "document_type": "concept",
            "component": "architecture",
            "title": "Prometheus Overview",
        },
        {
            "category": "promql",
            "name": "dummy-basics.md",
            "repo_key": "prometheus",
            "upstream_path": "docs/querying/basics.md",
            "document_type": "guide",
            "component": "promql",
            "title": "PromQL Basics",
        },
    ]

    mock_responses = {
        "docs/introduction/overview.md": b"---\ntitle: Upstream Overview\n---\n# Overview Content\nDetails here.",
        "docs/querying/basics.md": b"---\ntitle: Upstream Basics\n---\n# PromQL Basics\nrate(x[1m])",
    }

    def fake_fetch(url: str, **kwargs) -> str:
        for path_key, resp in mock_responses.items():
            if path_key in url:
                return resp.decode("utf-8")
        return "# Default Mock Content"

    with patch("advanced_rag.ingestion.prometheus_downloader.fetch_with_retries", side_effect=fake_fetch):
        summary = download_prometheus_docs(
            catalog=dummy_catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
        )

    assert summary["total_documents"] == 2
    assert summary["downloaded_count"] == 2
    assert summary["cached_count"] == 0

    assert (target_dir / "fundamentals" / "dummy-overview.md").exists()
    assert (target_dir / "promql" / "dummy-basics.md").exists()
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["total_documents"] == 2
    assert manifest["categories"] == {"fundamentals": 1, "promql": 1}
    assert manifest["spdx_license"] == "Apache-2.0"
    assert manifest["attribution"] == "Copyright The Prometheus Authors"


def test_download_prometheus_docs_incremental_caching(tmp_path):
    target_dir = tmp_path / "corpus" / "prometheus"
    manifest_path = tmp_path / "prometheus_manifest.json"

    dummy_catalog = [
        {
            "category": "fundamentals",
            "name": "caching-test.md",
            "repo_key": "docs",
            "upstream_path": "docs/concepts/data_model.md",
            "document_type": "concept",
            "component": "data-model",
            "title": "Data Model",
        }
    ]

    mock_content = b"---\ntitle: Data Model\n---\n# Data Model\nSeries and timestamps."

    with patch("advanced_rag.ingestion.prometheus_downloader.fetch_with_retries", return_value=mock_content.decode("utf-8")):
        s1 = download_prometheus_docs(catalog=dummy_catalog, target_dir=target_dir, manifest_path=manifest_path)
        assert s1["downloaded_count"] == 1
        assert s1["cached_count"] == 0

        # Second run should use cached content without fetching
        s2 = download_prometheus_docs(catalog=dummy_catalog, target_dir=target_dir, manifest_path=manifest_path)
        assert s2["downloaded_count"] == 0
        assert s2["cached_count"] == 1
        assert s2["total_documents"] == 1


def test_download_prometheus_docs_duplicate_detection(tmp_path):
    target_dir = tmp_path / "corpus" / "prometheus"
    manifest_path = tmp_path / "prometheus_manifest.json"

    # Two catalog entries returning identical cleaned bodies
    duplicate_catalog = [
        {
            "category": "fundamentals",
            "name": "doc-a.md",
            "repo_key": "docs",
            "upstream_path": "docs/a.md",
            "document_type": "concept",
            "component": "comp",
            "title": "Doc A",
        },
        {
            "category": "fundamentals",
            "name": "doc-b.md",
            "repo_key": "docs",
            "upstream_path": "docs/b.md",
            "document_type": "concept",
            "component": "comp",
            "title": "Doc B",
        },
    ]

    identical_content = "---\ntitle: Shared\n---\n# Exactly identical body text."

    with patch("advanced_rag.ingestion.prometheus_downloader.fetch_with_retries", return_value=identical_content):
        summary = download_prometheus_docs(catalog=duplicate_catalog, target_dir=target_dir, manifest_path=manifest_path)

    assert summary["downloaded_count"] == 1
    assert summary["skipped_duplicates_count"] == 1
    assert summary["total_documents"] == 1


def test_promote_staged_corpus_prometheus(tmp_path):
    staging_dir = tmp_path / "staging" / "prometheus"
    active_dir = tmp_path / "corpus" / "prometheus"
    staging_manifest = tmp_path / "staging" / "prometheus_manifest.json"
    active_manifest = tmp_path / "corpus" / "prometheus_manifest.json"

    doc_file = staging_dir / "fundamentals" / "overview.md"
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text("# Overview Staged", encoding="utf-8")

    manifest_data = {
        "manifest_version": "1.0",
        "documents": [
            {
                "local_path": doc_file.as_posix(),
                "name": "overview.md",
            }
        ],
    }
    staging_manifest.parent.mkdir(parents=True, exist_ok=True)
    staging_manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    count = promote_staged_corpus(
        staging_dir=staging_dir,
        active_dir=active_dir,
        staging_manifest=staging_manifest,
        active_manifest=active_manifest,
    )

    assert count == 1
    assert (active_dir / "fundamentals" / "overview.md").exists()
    assert active_manifest.exists()

    promoted_manifest = json.loads(active_manifest.read_text(encoding="utf-8"))
    assert promoted_manifest["documents"][0]["local_path"] == (active_dir / "fundamentals" / "overview.md").as_posix()


def test_validate_collected_prometheus_corpus_catches_unbalanced_code_fences(tmp_path):
    target_dir = tmp_path / "corpus" / "prometheus"
    manifest_path = tmp_path / "prometheus_manifest.json"
    doc_path = target_dir / "fundamentals" / "broken.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    unbalanced_body = "# Broken Doc\n```yaml\nunclosed code block"
    body_hash = compute_content_hash(unbalanced_body)
    fm = generate_frontmatter({
        "title": "Broken",
        "category": "fundamentals",
        "component": "arch",
        "prometheus_version": "3.16.0",
        "documentation_version": "3.16",
        "source_repository": PROMETHEUS_DOCS_REPO,
        "source_ref": PROMETHEUS_DOCS_REF,
        "source_commit": PROMETHEUS_DOCS_COMMIT,
        "source_url": "https://prometheus.io/docs/broken/",
        "source_version": "3.16.0",
        "retrieved_at": "2026-11-08T05:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "concept",
        "doc_type": "concept",
    })
    doc_path.write_text(f"{fm}\n\n{unbalanced_body}\n", encoding="utf-8")

    from advanced_rag.ingestion.prometheus_downloader import PROMETHEUS_DOCUMENT_CATALOG
    # Fill out manifest to match catalog length
    dummy_docs = [
        {
            "local_path": str(doc_path),
            "content_hash": body_hash,
            "source_ref": PROMETHEUS_DOCS_REF,
            "source_commit": PROMETHEUS_DOCS_COMMIT,
            "source_repository": PROMETHEUS_DOCS_REPO,
            "prometheus_version": "3.16.0",
        }
    ] * len(PROMETHEUS_DOCUMENT_CATALOG)

    manifest_data = {
        "manifest_version": "1.0",
        "documents": dummy_docs,
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_prometheus_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Unbalanced code fences" in str(exc_info.value)


def test_validate_collected_prometheus_corpus_catches_provenance_mismatch(tmp_path):
    target_dir = tmp_path / "corpus" / "prometheus"
    manifest_path = tmp_path / "prometheus_manifest.json"
    doc_path = target_dir / "fundamentals" / "mismatch.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    body = "# Mismatch Document\nClean body text."
    body_hash = compute_content_hash(body)
    fm = generate_frontmatter({
        "title": "Mismatch",
        "category": "fundamentals",
        "component": "arch",
        "prometheus_version": "3.16.0",
        "documentation_version": "3.16",
        "source_repository": PROMETHEUS_DOCS_REPO,
        "source_ref": PROMETHEUS_DOCS_REF,
        "source_commit": PROMETHEUS_DOCS_COMMIT,
        "source_url": "https://prometheus.io/docs/mismatch/",
        "source_version": "3.16.0",
        "retrieved_at": "2026-11-08T05:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "concept",
        "doc_type": "concept",
    })
    doc_path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")

    from advanced_rag.ingestion.prometheus_downloader import PROMETHEUS_DOCUMENT_CATALOG
    dummy_docs = [
        {
            "local_path": str(doc_path),
            "content_hash": body_hash,
            "source_ref": "wrong-ref",  # Provenance mismatch
            "source_commit": PROMETHEUS_DOCS_COMMIT,
            "source_repository": PROMETHEUS_DOCS_REPO,
            "prometheus_version": "3.16.0",
        }
    ] * len(PROMETHEUS_DOCUMENT_CATALOG)

    manifest_data = {
        "manifest_version": "1.0",
        "documents": dummy_docs,
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_prometheus_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Provenance mismatch" in str(exc_info.value)


def test_active_prometheus_corpus_provenance_and_immutability():
    assert DEFAULT_MANIFEST_PATH.exists(), "Active prometheus_manifest.json does not exist"

    manifest = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["corpus"] == "prometheus"
    assert manifest["prometheus_version"] == DEFAULT_PROMETHEUS_VERSION
    assert manifest["documentation_version"] == DEFAULT_DOCS_VERSION
    assert manifest["total_documents"] == 64
    assert len(manifest["documents"]) == 64
    assert manifest["spdx_license"] == SPDX_LICENSE
    assert manifest["attribution"] == ATTRIBUTION

    expected_categories = {
        "fundamentals": 12,
        "promql": 11,
        "alertmanager": 11,
        "kubernetes-monitoring": 9,
        "exporters": 11,
        "troubleshooting": 10,
    }
    assert manifest["categories"] == expected_categories

    seen_hashes: set[str] = set()
    for doc in manifest["documents"]:
        path = Path(doc["local_path"])
        assert path.exists(), f"Document missing: {path}"

        # Immutable source URL points to pinned commit
        assert (
            PROMETHEUS_DOCS_COMMIT in doc["immutable_source_url"]
            or PROMETHEUS_SERVER_COMMIT in doc["immutable_source_url"]
            or PROMETHEUS_ALERTMANAGER_COMMIT in doc["immutable_source_url"]
        )

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        assert closing is not None, f"Unclosed frontmatter in {path}"

        fm = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("\"'")

        assert fm["spdx_license"] == SPDX_LICENSE
        assert fm["attribution"] == ATTRIBUTION
        assert fm["documentation_version"] == DEFAULT_DOCS_VERSION

        # Check body integrity & hash match
        body = "\n".join(lines[closing + 1:]).strip()
        body_hash = compute_content_hash(body)
        assert body_hash == doc["content_hash"]
        assert body_hash == fm["content_hash"]
        assert body_hash not in seen_hashes
        seen_hashes.add(body_hash)

        # Code fences are balanced
        fences = [line for line in lines[closing + 1:] if line.strip().startswith("```")]
        assert len(fences) % 2 == 0, f"Unbalanced code fences in {path}"

    assert len(seen_hashes) == 64

