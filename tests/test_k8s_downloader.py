"""Unit tests for automated official Kubernetes documentation collection pipeline.

Validates:
- Downloader behavior with mocked network responses.
- Incremental download and caching logic.
- Duplicate detection and suppression.
- Metadata extraction, frontmatter formatting, and SPDX license tags.
- Retry behavior and error handling on network/HTTP failures.
- Manifest generation and structure.
- Markdown cleaning: Hugo shortcode transformation, skew handling, and code block preservation.
- Safe staging and promotion workflow.
- Quality validation checks (catching empty files, corrupted markdown, unbalanced fences).
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from advanced_rag.ingestion.k8s_downloader import (
    ATTRIBUTION,
    DEFAULT_BRANCH,
    DEFAULT_KUBERNETES_VERSION,
    DEFAULT_SOURCE_COMMIT,
    DEFAULT_SOURCE_REF,
    SPDX_LICENSE,
    clean_k8s_markdown,
    compute_content_hash,
    download_kubernetes_docs,
    fetch_with_retries,
    generate_frontmatter,
    get_canonical_k8s_url,
    promote_staged_corpus,
    validate_collected_corpus,
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


SAMPLE_RAW_DOC = """---
title: Pod Lifecycle
content_type: concept
weight: 30
---
<!-- overview -->
This page describes the lifecycle of a {{< glossary_tooltip text="Pod" term_id="pod" >}}.
This is for Kubernetes v{{< skew currentVersion >}}.

{{% note %}}
Pods are ephemeral.
{{% /note %}}

```yaml
# Version label for testing (for example v{{< skew currentVersion >}}):
apiVersion: v1
kind: Pod
metadata:
  name: nginx
spec:
  containers:
  - name: nginx
    image: nginx:1.14.2
```

{{% heading "whatsnext" %}}
Next steps.
"""


def test_clean_k8s_markdown_preserves_code_and_cleans_shortcodes():
    raw_meta, cleaned_body = clean_k8s_markdown(SAMPLE_RAW_DOC, version_label="v1.37")

    assert raw_meta["title"] == "Pod Lifecycle"
    assert raw_meta["content_type"] == "concept"

    # Hugo comments stripped
    assert "<!-- overview -->" not in cleaned_body

    # Glossary tooltip replaced by text
    assert "lifecycle of a Pod." in cleaned_body
    assert "glossary_tooltip" not in cleaned_body

    # Note transformed to blockquote
    assert "> **Note:** " in cleaned_body
    assert "Pods are ephemeral." in cleaned_body

    # Heading converted
    assert "## What's next" in cleaned_body

    # Skew macros converted both in prose and in code comments
    assert "Kubernetes v1.37." in cleaned_body
    assert "for example v1.37" in cleaned_body
    assert "skew currentVersion" not in cleaned_body

    # Code block preserved verbatim
    assert "```yaml" in cleaned_body
    assert "apiVersion: v1" in cleaned_body
    assert "image: nginx:1.14.2" in cleaned_body
    assert cleaned_body.count("```") == 2


def test_clean_k8s_markdown_handles_plain_term_id_tooltip():
    sample = "A {{< glossary_tooltip term_id=\"node-pressure\" >}} event happened."
    _, cleaned = clean_k8s_markdown(sample)
    assert cleaned == "A node pressure event happened."


def test_get_canonical_k8s_url():
    assert (
        get_canonical_k8s_url("content/en/docs/concepts/overview/_index.md")
        == "https://kubernetes.io/docs/concepts/overview/"
    )
    assert (
        get_canonical_k8s_url("content/en/docs/concepts/architecture/nodes.md")
        == "https://kubernetes.io/docs/concepts/architecture/nodes/"
    )
    assert (
        get_canonical_k8s_url("content/en/docs/tasks/debug/debug-application/debug-pods.md")
        == "https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/"
    )


def test_compute_content_hash():
    text1 = "# Hello Kubernetes\nSome content."
    text2 = "# Hello Kubernetes\nSome content."
    text3 = "# Hello Kubernetes\nDifferent content."
    h1 = compute_content_hash(text1)
    h2 = compute_content_hash(text2)
    h3 = compute_content_hash(text3)

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_generate_frontmatter_includes_all_required_fields():
    meta = {
        "title": "Nodes Architecture",
        "category": "architecture",
        "component": "node",
        "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
        "source_repository": "https://github.com/kubernetes/website",
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://kubernetes.io/docs/concepts/architecture/nodes/",
        "source_version": "1.37",
        "retrieved_at": "2026-11-07T20:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": "dummyhash123",
        "document_type": "concept",
        "doc_type": "concept",
    }
    fm = generate_frontmatter(meta)

    assert fm.startswith("---\n")
    assert fm.endswith("\n---")
    assert 'title: "Nodes Architecture"' in fm
    assert 'category: "architecture"' in fm
    assert 'kubernetes_version: "1.37"' in fm
    assert 'source_ref: "dev-1.37"' in fm
    assert f'source_commit: "{DEFAULT_SOURCE_COMMIT}"' in fm
    assert 'source_version: "1.37"' in fm
    assert 'spdx_license: "CC-BY-4.0"' in fm
    assert 'attribution: "Copyright The Kubernetes Authors"' in fm
    assert 'content_hash: "dummyhash123"' in fm


def test_fetch_with_retries_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockHTTPResponse(b"# Kubernetes Docs Content")
        content = fetch_with_retries("https://raw.githubusercontent.com/test/doc.md")
        assert content == "# Kubernetes Docs Content"
        mock_urlopen.assert_called_once()


def test_fetch_with_retries_recovers_after_transient_failure():
    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        # First call fails with 503, second succeeds
        err_503 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/test/doc.md",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = [err_503, MockHTTPResponse(b"# Recovered Content")]

        content = fetch_with_retries(
            "https://raw.githubusercontent.com/test/doc.md",
            max_retries=3,
            initial_delay=0.01,
        )
        assert content == "# Recovered Content"
        assert mock_urlopen.call_count == 2


def test_fetch_with_retries_raises_on_404_without_retrying():
    with patch("urllib.request.urlopen") as mock_urlopen:
        err_404 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/test/doc.md",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = err_404

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            fetch_with_retries("https://raw.githubusercontent.com/test/doc.md", max_retries=3)
        assert exc_info.value.code == 404
        assert mock_urlopen.call_count == 1


def test_fetch_with_retries_fails_after_max_retries():
    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        err_500 = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/test/doc.md",
            code=500,
            msg="Internal Error",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        mock_urlopen.side_effect = err_500

        with pytest.raises(RuntimeError) as exc_info:
            fetch_with_retries(
                "https://raw.githubusercontent.com/test/doc.md",
                max_retries=3,
                initial_delay=0.01,
            )
        assert "after 3 attempts" in str(exc_info.value)
        assert mock_urlopen.call_count == 3


def test_download_kubernetes_docs_end_to_end(tmp_path):
    catalog = [
        {
            "category": "workloads",
            "name": "pods.md",
            "upstream_path": "content/en/docs/concepts/workloads/pods/_index.md",
            "document_type": "concept",
            "component": "pod",
            "title": "Pods",
        },
        {
            "category": "architecture",
            "name": "nodes.md",
            "upstream_path": "content/en/docs/concepts/architecture/nodes.md",
            "document_type": "concept",
            "component": "node",
            "title": "Nodes",
        },
    ]

    target_dir = tmp_path / "corpus" / "kubernetes"
    manifest_path = tmp_path / "manifest.json"

    doc1_content = b"---\ntitle: Pods\n---\n# Pods Overview\nPods run containers in v1.37."
    doc2_content = b"---\ntitle: Nodes\n---\n# Nodes Overview\nNodes run pods in v1.37."

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockHTTPResponse(doc1_content),
            MockHTTPResponse(doc2_content),
        ]

        summary = download_kubernetes_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
            source_version="1.37",
        )

    assert summary["total_documents"] == 2
    assert summary["downloaded_documents"] == 2
    assert summary["cached_documents"] == 0
    assert summary["skipped_duplicates"] == 0
    assert summary["categories"] == {"workloads": 1, "architecture": 1}

    # Verify files created on disk
    pod_file = target_dir / "workloads" / "pods.md"
    node_file = target_dir / "architecture" / "nodes.md"
    assert pod_file.exists()
    assert node_file.exists()

    pod_text = pod_file.read_text(encoding="utf-8")
    assert 'title: "Pods"' in pod_text
    assert 'kubernetes_version: "1.37"' in pod_text
    assert 'source_ref: "dev-1.37"' in pod_text
    assert f'source_commit: "{DEFAULT_SOURCE_COMMIT}"' in pod_text
    assert 'source_version: "1.37"' in pod_text
    assert 'spdx_license: "CC-BY-4.0"' in pod_text
    assert "# Pods Overview" in pod_text

    # Verify manifest JSON
    assert manifest_path.exists()
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_obj["total_documents"] == 2
    assert manifest_obj["kubernetes_version"] == "1.37"
    assert manifest_obj["source_ref"] == "dev-1.37"
    assert manifest_obj["source_commit"] == DEFAULT_SOURCE_COMMIT
    assert manifest_obj["source_version"] == "1.37"
    assert len(manifest_obj["documents"]) == 2
    assert manifest_obj["documents"][0]["retrieval_status"] == "downloaded"
    assert manifest_obj["documents"][0]["immutable_source_url"].startswith(
        f"https://raw.githubusercontent.com/kubernetes/website/{DEFAULT_SOURCE_COMMIT}/"
    )

    # Verify validation passes
    val = validate_collected_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert val["status"] == "passed"
    assert val["documents_checked"] == 2


def test_download_kubernetes_docs_incremental_caching(tmp_path):
    catalog = [
        {
            "category": "workloads",
            "name": "pods.md",
            "upstream_path": "content/en/docs/concepts/workloads/pods/_index.md",
            "document_type": "concept",
            "component": "pod",
            "title": "Pods",
        }
    ]

    target_dir = tmp_path / "corpus" / "kubernetes"
    manifest_path = tmp_path / "manifest.json"
    doc_content = b"---\ntitle: Pods\n---\n# Pods Overview\nPods run containers."

    # First run: download
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = MockHTTPResponse(doc_content)
        summary1 = download_kubernetes_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
            source_version="release-1.37",
        )
    assert summary1["downloaded_documents"] == 1
    assert summary1["cached_documents"] == 0

    # Second run without force: should use cache without calling urlopen
    with patch("urllib.request.urlopen") as mock_urlopen:
        summary2 = download_kubernetes_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
            force=False,
            source_version="release-1.37",
        )
        mock_urlopen.assert_not_called()

    assert summary2["downloaded_documents"] == 0
    assert summary2["cached_documents"] == 1
    assert summary2["documents"][0]["retrieval_status"] == "cached"


def test_download_kubernetes_docs_detects_and_skips_duplicates(tmp_path):
    catalog = [
        {
            "category": "workloads",
            "name": "pods.md",
            "upstream_path": "content/en/docs/concepts/workloads/pods/_index.md",
            "document_type": "concept",
            "component": "pod",
            "title": "Pods",
        },
        {
            "category": "workloads",
            "name": "duplicate_pods.md",
            "upstream_path": "content/en/docs/concepts/workloads/pods/duplicate.md",
            "document_type": "concept",
            "component": "pod",
            "title": "Duplicate Pods",
        },
    ]

    target_dir = tmp_path / "corpus" / "kubernetes"
    manifest_path = tmp_path / "manifest.json"
    same_content = b"---\ntitle: Pods\n---\n# Pods Overview\nExact same content."

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = [
            MockHTTPResponse(same_content),
            MockHTTPResponse(same_content),
        ]
        summary = download_kubernetes_docs(
            catalog=catalog,
            target_dir=target_dir,
            manifest_path=manifest_path,
            source_version="release-1.37",
        )

    # Only 1 document should be registered, 1 skipped as duplicate
    assert summary["total_documents"] == 1
    assert summary["downloaded_documents"] == 1
    assert summary["skipped_duplicates"] == 1


def test_promote_staged_corpus_creates_backup_and_promotes(tmp_path):
    staging_dir = tmp_path / "staging_corpus" / "kubernetes"
    staging_manifest = tmp_path / "staging_corpus" / "kubernetes_manifest.json"
    active_dir = tmp_path / "corpus" / "kubernetes"
    active_manifest = tmp_path / "corpus" / "kubernetes_manifest.json"
    backup_dir = tmp_path / "backup_131"

    # Create dummy active corpus
    (active_dir / "fundamentals").mkdir(parents=True, exist_ok=True)
    (active_dir / "fundamentals" / "old.md").write_text("# Old 1.31 Content", encoding="utf-8")
    active_manifest.parent.mkdir(parents=True, exist_ok=True)
    active_manifest.write_text('{"manifest_version": "1.0", "source_version": "release-1.31", "documents": []}', encoding="utf-8")

    # Create valid staged document
    staged_cat = staging_dir / "fundamentals"
    staged_cat.mkdir(parents=True, exist_ok=True)
    staged_file = staged_cat / "what-is-kubernetes.md"
    body = "# What is Kubernetes v1.37"
    body_hash = compute_content_hash(body)
    fm = generate_frontmatter({
        "title": "What is Kubernetes",
        "category": "fundamentals",
        "component": "general",
        "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
        "source_repository": "https://github.com/kubernetes/website",
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://kubernetes.io/docs/concepts/overview/",
        "source_version": "1.37",
        "retrieved_at": "2026-11-07T21:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "concept",
    })
    staged_file.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")

    staging_manifest_data = {
        "manifest_version": "1.0",
        "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
        "source_repository": "https://github.com/kubernetes/website",
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_version": "1.37",
        "documents": [
            {
                "local_path": str(staged_file),
                "content_hash": body_hash,
                "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
                "source_ref": DEFAULT_SOURCE_REF,
                "source_commit": DEFAULT_SOURCE_COMMIT,
                "immutable_source_url": f"https://raw.githubusercontent.com/kubernetes/website/{DEFAULT_SOURCE_COMMIT}/content/en/docs/overview.md",
            }
        ],
    }
    staging_manifest.parent.mkdir(parents=True, exist_ok=True)
    staging_manifest.write_text(json.dumps(staging_manifest_data), encoding="utf-8")

    # Execute promote
    res = promote_staged_corpus(
        staging_dir=staging_dir,
        staging_manifest=staging_manifest,
        active_dir=active_dir,
        active_manifest=active_manifest,
        backup_131_dir=backup_dir,
    )

    assert res["status"] == "promoted"
    # Backup exists
    assert (backup_dir / "fundamentals" / "old.md").exists()
    assert (backup_dir / "kubernetes_manifest_1.31.json").exists()
    # Active corpus has new file
    assert (active_dir / "fundamentals" / "what-is-kubernetes.md").exists()


def test_validate_collected_corpus_catches_unbalanced_code_fences(tmp_path):
    target_dir = tmp_path / "corpus" / "kubernetes"
    manifest_path = tmp_path / "manifest.json"
    doc_path = target_dir / "workloads" / "broken.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    bad_body = "# Broken Code Block\n```yaml\nkey: value\n(missing closing fence)"
    body_hash = compute_content_hash(bad_body)
    frontmatter = generate_frontmatter({
        "title": "Broken",
        "category": "workloads",
        "component": "pod",
        "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
        "source_repository": "https://github.com/kubernetes/website",
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://kubernetes.io/docs/broken/",
        "source_version": "1.37",
        "retrieved_at": "2026-11-07T20:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "concept",
    })
    doc_path.write_text(f"{frontmatter}\n\n{bad_body}\n", encoding="utf-8")

    manifest_data = {
        "manifest_version": "1.0",
        "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
        "source_repository": "https://github.com/kubernetes/website",
        "source_ref": DEFAULT_SOURCE_REF,
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_version": "1.37",
        "documents": [
            {
                "local_path": str(doc_path),
                "content_hash": body_hash,
                "kubernetes_version": DEFAULT_KUBERNETES_VERSION,
                "source_ref": DEFAULT_SOURCE_REF,
                "source_commit": DEFAULT_SOURCE_COMMIT,
                "immutable_source_url": f"https://raw.githubusercontent.com/kubernetes/website/{DEFAULT_SOURCE_COMMIT}/content/en/docs/broken.md",
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Unbalanced code fences" in str(exc_info.value)


def test_validate_collected_corpus_catches_missing_file(tmp_path):
    target_dir = tmp_path / "corpus" / "kubernetes"
    manifest_path = tmp_path / "manifest.json"

    manifest_data = {
        "manifest_version": "1.0",
        "documents": [
            {
                "local_path": str(target_dir / "non_existent.md"),
                "content_hash": "abc",
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Missing file on disk" in str(exc_info.value)


def test_validate_collected_corpus_catches_provenance_mismatch(tmp_path):
    target_dir = tmp_path / "corpus" / "kubernetes"
    manifest_path = tmp_path / "manifest.json"
    doc_path = target_dir / "workloads" / "mismatch.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    body = "# Mismatch Doc\nTesting provenance validation."
    body_hash = compute_content_hash(body)
    fm = generate_frontmatter({
        "title": "Mismatch",
        "category": "workloads",
        "component": "pod",
        "kubernetes_version": "1.37",
        "source_repository": "https://github.com/kubernetes/website",
        "source_ref": "dev-1.37",
        "source_commit": DEFAULT_SOURCE_COMMIT,
        "source_url": "https://kubernetes.io/docs/mismatch/",
        "source_version": "1.37",
        "retrieved_at": "2026-11-07T20:00:00Z",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "content_hash": body_hash,
        "document_type": "concept",
    })
    doc_path.write_text(f"{fm}\n\n{body}\n", encoding="utf-8")

    # Manifest states a different kubernetes_version
    manifest_data = {
        "manifest_version": "1.0",
        "documents": [
            {
                "local_path": str(doc_path),
                "content_hash": body_hash,
                "kubernetes_version": "1.36",  # Mismatch!
                "source_ref": "dev-1.37",
                "source_commit": DEFAULT_SOURCE_COMMIT,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        validate_collected_corpus(corpus_root=target_dir, manifest_path=manifest_path)
    assert "Provenance mismatch" in str(exc_info.value)


def test_active_corpus_provenance_schema_and_immutability():
    manifest_path = Path("data/corpus/kubernetes_manifest.json")
    assert manifest_path.exists(), "Active manifest does not exist"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["kubernetes_version"] == "1.37"
    assert manifest["source_ref"] == "dev-1.37"
    assert manifest["source_commit"] == "aebe074739e78e9111943ac285df6ae3a6319f69"
    assert manifest["source_version"] == "1.37"
    assert manifest["total_documents"] == 64
    assert len(manifest["documents"]) == 64

    seen_hashes: set[str] = set()
    for doc in manifest["documents"]:
        path = Path(doc["local_path"])
        assert path.exists(), f"Document file missing: {path}"

        # Check immutable raw URL
        assert doc["immutable_source_url"].startswith(
            "https://raw.githubusercontent.com/kubernetes/website/aebe074739e78e9111943ac285df6ae3a6319f69/"
        )
        assert doc["kubernetes_version"] == "1.37"
        assert doc["source_ref"] == "dev-1.37"
        assert doc["source_commit"] == "aebe074739e78e9111943ac285df6ae3a6319f69"

        # Check frontmatter and body
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        assert closing is not None, f"Unclosed frontmatter in {path}"

        fm = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("\"'")

        assert fm["kubernetes_version"] == "1.37"
        assert fm["source_ref"] == "dev-1.37"
        assert fm["source_commit"] == "aebe074739e78e9111943ac285df6ae3a6319f69"
        assert fm["source_version"] == "1.37"
        assert fm["spdx_license"] == "CC-BY-4.0"
        assert fm["attribution"] == "Copyright The Kubernetes Authors"

        # Check body integrity
        body = "\n".join(lines[closing + 1:]).strip()
        body_hash = compute_content_hash(body)
        assert body_hash == doc["content_hash"]
        assert body_hash == fm["content_hash"]
        assert body_hash not in seen_hashes
        seen_hashes.add(body_hash)

    assert len(seen_hashes) == 64
