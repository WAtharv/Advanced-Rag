"""Unit tests for automated official Helm documentation collection pipeline.

Validates:
- Downloader behavior with mocked network responses.
- Incremental download and caching logic.
- Duplicate detection and suppression.
- Metadata extraction, frontmatter formatting, and SPDX license tags.
- Retry behavior and error handling on network/HTTP failures.
- Manifest generation, category distribution, and structure.
- Markdown cleaning: MDX component stripping, admonition conversion, and Go template preservation.
- Quality validation checks (catching empty files, corrupted markdown, unbalanced fences, provenance mismatch).
- Active corpus provenance schema and immutability verification.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from advanced_rag.ingestion.helm_downloader import (
    ATTRIBUTION,
    DEFAULT_BRANCH,
    DEFAULT_HELM_VERSION,
    DEFAULT_REPO,
    DEFAULT_SOURCE_COMMIT,
    DEFAULT_SOURCE_REF,
    SPDX_LICENSE,
    clean_helm_markdown,
    compute_content_hash,
    download_helm_docs,
    fetch_with_retries,
    generate_frontmatter,
    get_canonical_helm_url,
    validate_collected_helm_corpus,
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


SAMPLE_RAW_HELM_MDX = """---
title: Using Helm
description: Explains the basics of Helm.
sidebar_position: 4
---

import Helm4 from "/docs/_v4-in-progress.mdx"

<Helm4/>

:::note Note Title
Helm 4 uses OCI registries by default.
:::

:::warning
Always backup your release secrets before upgrading.
:::

This guide explains how to use Helm.

```yaml
# Helm template example that must remain 100% intact:
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-config
data:
  favorite_drink: {{ .Values.favorite.drink | default "tea" | quote }}
  {{- range .Values.pizzas }}
  - {{ . | quote }}
  {{- end }}
```

<GetVersion majorVersion="4" label={false} link={true} />
"""


def test_clean_helm_markdown_preserves_templates_and_cleans_mdx():
    raw_meta, cleaned_body = clean_helm_markdown(SAMPLE_RAW_HELM_MDX, version_label="v4.3.1")

    assert raw_meta["title"] == "Using Helm"
    assert raw_meta["description"] == "Explains the basics of Helm."

    # MDX imports and components stripped
    assert "import Helm4" not in cleaned_body
    assert "<Helm4/>" not in cleaned_body

    # Admonitions converted to blockquotes
    assert "> **Note Title:** " in cleaned_body
    assert "Helm 4 uses OCI registries by default." in cleaned_body
    assert "> **Warning:** " in cleaned_body
    assert "Always backup your release secrets before upgrading." in cleaned_body

    # Version macro replaced
    assert "v4.3.1" in cleaned_body
    assert "<GetVersion" not in cleaned_body

    # Go template code block strictly preserved verbatim
    assert "```yaml" in cleaned_body
    assert 'name: {{ .Release.Name }}-config' in cleaned_body
    assert 'favorite_drink: {{ .Values.favorite.drink | default "tea" | quote }}' in cleaned_body
    assert '{{- range .Values.pizzas }}' in cleaned_body
    assert cleaned_body.count("```") == 2


def test_get_canonical_helm_url():
    assert (
        get_canonical_helm_url("docs/intro/introduction.md")
        == "https://helm.sh/docs/intro/introduction/"
    )
    assert (
        get_canonical_helm_url("docs/intro/install.mdx")
        == "https://helm.sh/docs/intro/install/"
    )
    assert (
        get_canonical_helm_url("docs/glossary/index.mdx")
        == "https://helm.sh/docs/glossary/"
    )
    assert (
        get_canonical_helm_url("versioned_docs/version-3/faq/troubleshooting.md")
        == "https://helm.sh/docs/v3/faq/troubleshooting/"
    )
    assert (
        get_canonical_helm_url("docs/overview.md")
        == "https://helm.sh/docs/overview/"
    )


def test_compute_content_hash():
    t1 = "# Helm Overview\nContent A."
    t2 = "# Helm Overview\nContent A."
    t3 = "# Helm Overview\nContent B."
    h1 = compute_content_hash(t1)
    h2 = compute_content_hash(t2)
    h3 = compute_content_hash(t3)

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_generate_frontmatter_helm_required_fields():
    meta = {
        "title": "Introduction to Helm",
        "category": "fundamentals",
        "component": "architecture",
        "helm_version": DEFAULT_HELM_VERSION,
        "source_repository": DEFAULT_REPO,
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://helm.sh/docs/intro/introduction/",
        "source_version": DEFAULT_HELM_VERSION,
        "retrieved_at": "2026-11-08T04:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": "dummyhash456",
        "document_type": "concept",
        "doc_type": "concept",
    }
    fm = generate_frontmatter(meta)

    assert fm.startswith("---\n")
    assert fm.endswith("\n---")
    assert 'title: "Introduction to Helm"' in fm
    assert 'category: "fundamentals"' in fm
    assert f'helm_version: "{DEFAULT_HELM_VERSION}"' in fm
    assert f'source_commit: "{DEFAULT_SOURCE_COMMIT}"' in fm
    assert f'source_ref: "{DEFAULT_SOURCE_REF}"' in fm
    assert f'source_repository: "{DEFAULT_REPO}"' in fm
    assert 'source_url: "https://helm.sh/docs/intro/introduction/"' in fm
    assert 'spdx_license: "CC-BY-4.0"' in fm
    assert 'attribution: "Copyright The Helm Authors"' in fm
    assert 'content_hash: "dummyhash456"' in fm


def test_fetch_with_retries_helm_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockHTTPResponse(b"# Helm Content")
        res = fetch_with_retries("https://raw.githubusercontent.com/helm/helm-www/test.md")
        assert res == "# Helm Content"
        mock_urlopen.assert_called_once()


def test_fetch_with_retries_helm_recovers_transient():
    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        err_503 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/helm/helm-www/test.md",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = [err_503, MockHTTPResponse(b"# Recovered Helm Docs")]
        res = fetch_with_retries(
            "https://raw.githubusercontent.com/helm/helm-www/test.md",
            max_retries=3,
            initial_delay=0.01,
        )
        assert res == "# Recovered Helm Docs"
        assert mock_urlopen.call_count == 2


def test_fetch_with_retries_helm_404_raises_immediately():
    with patch("urllib.request.urlopen") as mock_urlopen:
        err_404 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/helm/helm-www/test.md",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = err_404
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            fetch_with_retries("https://raw.githubusercontent.com/helm/helm-www/test.md", max_retries=3)
        assert exc_info.value.code == 404
        assert mock_urlopen.call_count == 1


def test_fetch_with_retries_helm_exhausts_retries():
    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        err_500 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/helm/helm-www/test.md",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = err_500
        with pytest.raises(RuntimeError) as exc_info:
            fetch_with_retries(
                "https://raw.githubusercontent.com/helm/helm-www/test.md",
                max_retries=3,
                initial_delay=0.01,
            )
        assert "after 3 attempts" in str(exc_info.value)
        assert mock_urlopen.call_count == 3


def test_download_helm_docs_end_to_end(tmp_path):
    catalog = [
        {
            "category": "fundamentals",
            "name": "what-is-helm.md",
            "upstream_path": "docs/intro/introduction.md",
            "document_type": "concept",
            "component": "architecture",
            "title": "Introduction to Helm",
        },
        {
            "category": "charts",
            "name": "charts-guide.md",
            "upstream_path": "docs/topics/charts.mdx",
            "document_type": "guide",
            "component": "chart",
            "title": "Helm Charts Guide",
        },
    ]

    target_dir = tmp_path / "corpus" / "helm"
    manifest_path = tmp_path / "helm_manifest.json"

    doc1_raw = b"---\ntitle: Intro\n---\n# Introduction to Helm\nHelm is the Kubernetes package manager."
    doc2_raw = b"---\ntitle: Charts\n---\n# Helm Charts Guide\nA chart is a bundle of manifests."

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockHTTPResponse(doc1_raw),
            MockHTTPResponse(doc2_raw),
        ]
        summary = download_helm_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
        )

    assert summary["total_documents"] == 2
    assert summary["downloaded_documents"] == 2
    assert summary["cached_documents"] == 0
    assert summary["skipped_duplicates"] == 0
    assert summary["categories"] == {"fundamentals": 1, "charts": 1}

    # Verify files created on disk
    f1 = target_dir / "fundamentals" / "what-is-helm.md"
    f2 = target_dir / "charts" / "charts-guide.md"
    assert f1.exists()
    assert f2.exists()

    t1 = f1.read_text(encoding="utf-8")
    assert 'title: "Introduction to Helm"' in t1
    assert f'helm_version: "{DEFAULT_HELM_VERSION}"' in t1
    assert f'source_commit: "{DEFAULT_SOURCE_COMMIT}"' in t1
    assert 'source_ref: "main"' in t1

    # Verify manifest JSON
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_obj["total_documents"] == 2
    assert manifest_obj["helm_version"] == DEFAULT_HELM_VERSION
    assert manifest_obj["source_commit"] == DEFAULT_SOURCE_COMMIT
    assert manifest_obj["source_ref"] == "main"
    assert len(manifest_obj["documents"]) == 2
    assert manifest_obj["documents"][0]["immutable_source_url"].startswith(
        f"https://raw.githubusercontent.com/helm/helm-www/{DEFAULT_SOURCE_COMMIT}/"
    )

    # Verify validation passes
    val = validate_collected_helm_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert val["status"] == "passed"
    assert val["documents_checked"] == 2


def test_download_helm_docs_incremental_caching(tmp_path):
    catalog = [
        {
            "category": "fundamentals",
            "name": "what-is-helm.md",
            "upstream_path": "docs/intro/introduction.md",
            "document_type": "concept",
            "component": "architecture",
            "title": "Introduction to Helm",
        }
    ]

    target_dir = tmp_path / "corpus" / "helm"
    manifest_path = tmp_path / "helm_manifest.json"
    doc_raw = b"---\ntitle: Intro\n---\n# Introduction to Helm\nHelm is the Kubernetes package manager."

    # First download
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockHTTPResponse(doc_raw)
        s1 = download_helm_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
        )
    assert s1["downloaded_documents"] == 1
    assert s1["cached_documents"] == 0

    # Second run without force
    with patch("urllib.request.urlopen") as mock_urlopen:
        s2 = download_helm_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
            force=False,
        )
        mock_urlopen.assert_not_called()

    assert s2["downloaded_documents"] == 0
    assert s2["cached_documents"] == 1
    assert s2["documents"][0]["retrieval_status"] == "cached"


def test_download_helm_docs_duplicate_detection(tmp_path):
    catalog = [
        {
            "category": "fundamentals",
            "name": "doc1.md",
            "upstream_path": "docs/a.md",
            "document_type": "concept",
            "component": "general",
            "title": "Doc 1",
        },
        {
            "category": "fundamentals",
            "name": "doc2.md",
            "upstream_path": "docs/b.md",
            "document_type": "concept",
            "component": "general",
            "title": "Doc 2",
        },
    ]

    target_dir = tmp_path / "corpus" / "helm"
    manifest_path = tmp_path / "helm_manifest.json"
    same_content = b"# Identical content in both docs"

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockHTTPResponse(same_content),
            MockHTTPResponse(same_content),
        ]
        summary = download_helm_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
        )

    # 1 registered, 1 skipped as duplicate
    assert summary["total_documents"] == 1
    assert summary["downloaded_documents"] == 1
    assert summary["skipped_duplicates"] == 1


def test_validate_collected_helm_corpus_catches_unbalanced_code_fences(tmp_path):
    target_dir = tmp_path / "corpus" / "helm"
    manifest_path = tmp_path / "helm_manifest.json"
    doc_path = target_dir / "templating" / "broken.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    bad_body = "# Broken Helm Template\n```yaml\nkey: value\n(unclosed code block)"
    body_hash = compute_content_hash(bad_body)
    fm = generate_frontmatter({
        "title": "Broken",
        "category": "templating",
        "component": "templates",
        "helm_version": DEFAULT_HELM_VERSION,
        "source_repository": DEFAULT_REPO,
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://helm.sh/docs/broken/",
        "source_version": DEFAULT_HELM_VERSION,
        "retrieved_at": "2026-11-08T04:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "guide",
    })
    doc_path.write_text(f"{fm}\n\n{bad_body}\n", encoding="utf-8")

    manifest_data = {
        "manifest_version": "1.0",
        "helm_version": DEFAULT_HELM_VERSION,
        "source_repository": DEFAULT_REPO,
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "documents": [
            {
                "local_path": str(doc_path),
                "content_hash": body_hash,
                "helm_version": DEFAULT_HELM_VERSION,
                "source_ref": DEFAULT_SOURCE_REF,
                "source_commit": DEFAULT_SOURCE_COMMIT,
                "immutable_source_url": f"https://raw.githubusercontent.com/helm/helm-www/{DEFAULT_SOURCE_COMMIT}/docs/broken.md",
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_helm_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Unbalanced code fences" in str(exc_info.value)


def test_validate_collected_helm_corpus_catches_provenance_mismatch(tmp_path):
    target_dir = tmp_path / "corpus" / "helm"
    manifest_path = tmp_path / "helm_manifest.json"
    doc_path = target_dir / "charts" / "mismatch.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    body = "# Mismatch Document\nClean body."
    body_hash = compute_content_hash(body)
    fm = generate_frontmatter({
        "title": "Mismatch",
        "category": "charts",
        "component": "chart",
        "helm_version": "4.3.1",
        "source_repository": DEFAULT_REPO,
        "source_ref": "main",
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://helm.sh/docs/charts/",
        "source_version": "4.3.1",
        "retrieved_at": "2026-11-08T04:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "guide",
    })
    doc_path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")

    # Manifest claims different helm_version
    manifest_data = {
        "manifest_version": "1.0",
        "documents": [
            {
                "local_path": str(doc_path),
                "content_hash": body_hash,
                "helm_version": "3.16.0",  # Mismatch!
                "source_ref": "main",
                "source_commit": DEFAULT_SOURCE_COMMIT,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_helm_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Provenance mismatch" in str(exc_info.value)


def test_active_helm_corpus_provenance_and_immutability():
    manifest_path = Path("data/corpus/helm_manifest.json")
    assert manifest_path.exists(), "Active helm_manifest.json does not exist"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["helm_version"] == "4.3.1"
    assert manifest["source_ref"] == "main"
    assert manifest["source_commit"] == "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
    assert manifest["total_documents"] == 64
    assert len(manifest["documents"]) == 64

    seen_hashes: set[str] = set()
    for doc in manifest["documents"]:
        path = Path(doc["local_path"])
        assert path.exists(), f"Document missing: {path}"

        # Check immutable raw URL
        assert doc["immutable_source_url"].startswith(
            "https://raw.githubusercontent.com/helm/helm-www/60d4db89421147aec9fc3fcef9e2d49e2729f50b/"
        )
        expected_version = "3.22.1" if doc["name"] == "troubleshooting-faq.md" else "4.3.1"
        assert doc["helm_version"] == expected_version
        assert doc["version"] == expected_version
        assert doc["source_ref"] == "main"
        assert doc["source_commit"] == "60d4db89421147aec9fc3fcef9e2d49e2729f50b"

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        assert closing is not None, f"Unclosed frontmatter in {path}"

        fm = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("\"'")

        assert fm["helm_version"] == expected_version
        assert fm["source_version"] == expected_version
        assert fm["source_ref"] == "main"
        assert fm["source_commit"] == "60d4db89421147aec9fc3fcef9e2d49e2729f50b"
        assert fm["spdx_license"] == "CC-BY-4.0"
        assert fm["attribution"] == "Copyright The Helm Authors"

        # Check body integrity
        body = "\n".join(lines[closing + 1:]).strip()
        body_hash = compute_content_hash(body)
        assert body_hash == doc["content_hash"]
        assert body_hash == fm["content_hash"]
        assert body_hash not in seen_hashes
        seen_hashes.add(body_hash)

    assert len(seen_hashes) == 64

