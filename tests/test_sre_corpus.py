"""Unit and integration tests for SRE knowledge corpus (Stage 2.6).

Validates:
- Document discovery via DocumentRegistry (60 SRE documents, 252 total built-in).
- Frontmatter schema completeness (title, incident_id, severity, category, component,
  document_type, doc_type, source_type, applicable_versions, authoring_date, spdx_license,
  attribution, content_hash).
- Source attribution (clearly marked as project-authored-sre, not falsely attributed to upstreams).
- Prominent educational labelling on all simulated postmortems and incident case studies.
- Content hash agreement and duplicate detection across the corpus.
- Balanced code fences and MarkdownExtractor compatibility.
- Preservation of existing official corpora (Kubernetes, Helm, Prometheus).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from advanced_rag.ingestion.extractors.markdown import MarkdownExtractor
from advanced_rag.ingestion.registry import DocumentRegistry
from advanced_rag.ingestion.sre_corpus_builder import (
    ALL_SRE_DOCS,
    ATTRIBUTION,
    DEFAULT_SRE_CORPUS_DIR,
    DEFAULT_SRE_MANIFEST_PATH,
    SPDX_LICENSE,
    compute_content_hash,
    validate_sre_corpus,
)


def test_sre_manifest_exists_and_valid():
    assert DEFAULT_SRE_MANIFEST_PATH.exists(), f"Manifest missing at {DEFAULT_SRE_MANIFEST_PATH}"
    manifest = json.loads(DEFAULT_SRE_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["corpus"] == "sre"
    assert manifest["total_documents"] == 60
    assert len(manifest["documents"]) == 60
    assert manifest["spdx_license"] == SPDX_LICENSE
    assert manifest["attribution"] == ATTRIBUTION

    expected_categories = {
        "runbooks/kubernetes": 14,
        "runbooks/networking": 6,
        "runbooks/storage": 5,
        "runbooks/helm": 6,
        "runbooks/prometheus": 6,
        "runbooks/security": 5,
        "response-procedures": 9,
        "templates": 3,
        "postmortems": 4,
        "incidents": 2,
    }
    assert manifest["categories"] == expected_categories


def test_sre_corpus_validation_passes():
    result = validate_sre_corpus(DEFAULT_SRE_CORPUS_DIR, DEFAULT_SRE_MANIFEST_PATH)
    assert result["status"] == "passed"
    assert result["verified_documents"] == 60


def test_document_registry_discovers_all_sre_documents():
    registry = DocumentRegistry()
    built_in = registry.get_built_in_documents()

    # Total: 64 k8s + 64 helm + 64 prometheus + 60 sre = 252
    assert len(built_in) == 252

    sre_docs = [d for d in built_in if d["source"].startswith("sre/")]
    assert len(sre_docs) == 60

    category_counts: dict[str, int] = {}
    for d in sre_docs:
        parts = d["source"].split("/")
        cat = "/".join(parts[1:-1])
        category_counts[cat] = category_counts.get(cat, 0) + 1

    assert category_counts["runbooks/kubernetes"] == 14
    assert category_counts["runbooks/networking"] == 6
    assert category_counts["runbooks/storage"] == 5
    assert category_counts["runbooks/helm"] == 6
    assert category_counts["runbooks/prometheus"] == 6
    assert category_counts["runbooks/security"] == 5
    assert category_counts["response-procedures"] == 9
    assert category_counts["templates"] == 3
    assert category_counts["postmortems"] == 4
    assert category_counts["incidents"] == 2


def test_sre_frontmatter_schema_and_metadata_completeness():
    manifest = json.loads(DEFAULT_SRE_MANIFEST_PATH.read_text(encoding="utf-8"))
    seen_hashes: set[str] = set()

    required_keys = [
        "title",
        "incident_id",
        "severity",
        "category",
        "component",
        "document_type",
        "doc_type",
        "source_type",
        "applicable_versions",
        "authoring_date",
        "spdx_license",
        "attribution",
        "content_hash",
    ]

    for doc in manifest["documents"]:
        path = Path(doc["local_path"])
        assert path.exists(), f"File missing: {path}"

        raw = path.read_text(encoding="utf-8")
        assert raw.startswith("---")
        lines = raw.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        assert closing is not None, f"Unclosed frontmatter in {path}"

        fm = {}
        for line in lines[1:closing]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip("\"'")

        for rk in required_keys:
            assert rk in fm, f"Missing key '{rk}' in {path}"
            assert fm[rk], f"Empty key '{rk}' in {path}"

        assert fm["source_type"] == "project-authored-sre"
        assert fm["spdx_license"] == SPDX_LICENSE
        assert fm["attribution"] == ATTRIBUTION

        # Body hash match
        body = "\n".join(lines[closing + 1:]).strip()
        body_hash = compute_content_hash(body)
        assert body_hash == doc["content_hash"]
        assert body_hash == fm["content_hash"]
        assert body_hash not in seen_hashes
        seen_hashes.add(body_hash)

        # Code fences balance
        fences = [l for l in lines[closing + 1:] if l.strip().startswith("```")]
        assert len(fences) % 2 == 0, f"Unbalanced code fences in {path}"

    assert len(seen_hashes) == 60


def test_simulated_incidents_have_prominent_disclaimer():
    manifest = json.loads(DEFAULT_SRE_MANIFEST_PATH.read_text(encoding="utf-8"))
    simulated_docs = [d for d in manifest["documents"] if d["category"] in ("postmortems", "incidents")]

    assert len(simulated_docs) == 6  # 4 postmortems + 2 incidents

    for doc in simulated_docs:
        path = Path(doc["local_path"])
        content = path.read_text(encoding="utf-8")
        assert "SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT." in content, (
            f"Missing required simulation disclaimer in {path}"
        )
        assert doc["is_simulated"] is True


def test_runbooks_contain_required_operational_sections():
    manifest = json.loads(DEFAULT_SRE_MANIFEST_PATH.read_text(encoding="utf-8"))
    runbooks = [d for d in manifest["documents"] if d["category"].startswith("runbooks")]

    assert len(runbooks) == 42

    required_subheadings = [
        "Incident Identifier",
        "Symptoms",
        "Common Causes",
        "Diagnostic Procedure",
        "Remediation Steps",
        "Verification After Remediation",
    ]

    for rb in runbooks:
        path = Path(rb["local_path"])
        content = path.read_text(encoding="utf-8")
        for sh in required_subheadings:
            assert sh.lower() in content.lower(), f"Runbook {path.name} missing section '{sh}'"


def test_markdown_extractor_extracts_sre_documents():
    extractor = MarkdownExtractor()
    manifest = json.loads(DEFAULT_SRE_MANIFEST_PATH.read_text(encoding="utf-8"))

    for doc_entry in manifest["documents"]:
        path = Path(doc_entry["local_path"])
        doc = extractor.extract(path, root=Path("data/corpus"))

        assert doc.title == doc_entry["title"]
        assert doc.doc_type == doc_entry["doc_type"]
        assert doc.metadata["incident_id"] == doc_entry["incident_id"]
        assert doc.metadata["source_type"] == "project-authored-sre"
        assert len(doc.body) > 100


def test_existing_official_corpora_remain_intact():
    # Verify Kubernetes 1.37
    k8s_manifest = json.loads(Path("data/corpus/kubernetes_manifest.json").read_text(encoding="utf-8"))
    assert k8s_manifest["total_documents"] == 64
    assert k8s_manifest["kubernetes_version"] == "1.37"

    # Verify Helm
    helm_manifest = json.loads(Path("data/corpus/helm_manifest.json").read_text(encoding="utf-8"))
    assert helm_manifest["total_documents"] == 64

    # Verify Prometheus
    prom_manifest = json.loads(Path("data/corpus/prometheus_manifest.json").read_text(encoding="utf-8"))
    assert prom_manifest["total_documents"] == 64

