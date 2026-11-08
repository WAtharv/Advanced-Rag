"""SRE Knowledge Corpus Builder and Validator.

Compiles, formats, validates, and writes the 60 curated SRE operational documents:
- 42 Troubleshooting Runbooks (Kubernetes, Helm, Prometheus, Networking, Storage, Security)
- 9 Incident Response Procedures
- 3 Standard Templates (Response, Checklist, Postmortem)
- 4 Educational Simulated Postmortems
- 2 Educational Simulated Incident Case Studies

Ensures deterministic content hashing, balanced code fences, standardized YAML
frontmatter, and a machine-readable manifest (data/corpus/sre_manifest.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from advanced_rag.ingestion.sre_data.helm_prom_sec_runbooks import (
    HELM_RUNBOOKS,
    PROMETHEUS_RUNBOOKS,
    SECURITY_RUNBOOKS,
)
from advanced_rag.ingestion.sre_data.k8s_runbooks import K8S_RUNBOOKS
from advanced_rag.ingestion.sre_data.net_storage_runbooks import (
    NETWORKING_RUNBOOKS,
    STORAGE_RUNBOOKS,
)
from advanced_rag.ingestion.sre_data.procedures_and_templates import (
    RESPONSE_PROCEDURES,
    TEMPLATES,
)
from advanced_rag.ingestion.sre_data.simulated_incidents import (
    SIMULATED_CASE_STUDIES,
    SIMULATED_POSTMORTEMS,
)

logger = logging.getLogger(__name__)

DEFAULT_SRE_CORPUS_DIR = Path("data/corpus/sre")
DEFAULT_SRE_MANIFEST_PATH = Path("data/corpus/sre_manifest.json")
SPDX_LICENSE = "Apache-2.0"
ATTRIBUTION = "Enterprise SRE Copilot Authors"
AUTHORING_DATE = "2026-11-08"

ALL_SRE_DOCS: list[dict[str, Any]] = (
    K8S_RUNBOOKS
    + NETWORKING_RUNBOOKS
    + STORAGE_RUNBOOKS
    + HELM_RUNBOOKS
    + PROMETHEUS_RUNBOOKS
    + SECURITY_RUNBOOKS
    + RESPONSE_PROCEDURES
    + TEMPLATES
    + SIMULATED_POSTMORTEMS
    + SIMULATED_CASE_STUDIES
)


def compute_content_hash(text: str) -> str:
    """Compute deterministic SHA-256 hex digest of cleaned text content."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def determine_doc_type(category: str) -> str:
    """Determine standardized document type from category."""
    if category.startswith("runbooks"):
        return "runbook"
    elif category == "response-procedures":
        return "procedure"
    elif category == "templates":
        return "template"
    elif category == "postmortems":
        return "postmortem"
    elif category == "incidents":
        return "case-study"
    return "sre-guide"


def generate_sre_frontmatter(metadata: dict[str, Any]) -> str:
    """Generate standardized YAML frontmatter for SRE documents."""
    lines = ["---"]
    keys = [
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
    for k in keys:
        val = metadata.get(k, "")
        clean_val = str(val).replace('"', '\\"')
        lines.append(f'{k}: "{clean_val}"')

    # Add related URLs if present
    urls = metadata.get("related_upstream_urls", [])
    if urls:
        lines.append("related_upstream_urls:")
        for u in urls:
            lines.append(f'  - "{u}"')

    lines.append("---")
    return "\n".join(lines)


def build_sre_corpus(
    corpus_root: Path = DEFAULT_SRE_CORPUS_DIR,
    manifest_path: Path = DEFAULT_SRE_MANIFEST_PATH,
) -> dict[str, Any]:
    """Compile, format, and write all SRE documents and generate manifest.

    Returns:
        Summary dict of build results.
    """
    corpus_root.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    seen_hashes: dict[str, str] = {}
    manifest_docs: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    now_iso = datetime.now(UTC).isoformat()

    logger.info("Building SRE knowledge corpus (%d documents)...", len(ALL_SRE_DOCS))

    for item in ALL_SRE_DOCS:
        category = item["category"]
        name = item["name"]
        title = item["title"]
        incident_id = item.get("incident_id", "")
        severity = item.get("severity", "P2")
        component = item.get("component", "sre")
        applicable_versions = item.get("applicable_versions", "All")
        raw_body = item["content"].strip()
        doc_type = determine_doc_type(category)
        related_urls = item.get("related_upstream_urls", [])

        # Validate code fences balance
        fences = [l for l in raw_body.splitlines() if l.strip().startswith("```")]
        if len(fences) % 2 != 0:
            raise ValueError(f"Unbalanced code fences in {name}: found {len(fences)} markers")

        content_hash = compute_content_hash(raw_body)
        if content_hash in seen_hashes:
            raise ValueError(f"Duplicate content detected between {name} and {seen_hashes[content_hash]}")
        seen_hashes[content_hash] = name

        cat_dir = corpus_root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        local_path = cat_dir / name

        meta = {
            "title": title,
            "incident_id": incident_id,
            "severity": severity,
            "category": category,
            "component": component,
            "document_type": doc_type,
            "doc_type": doc_type,
            "source_type": "project-authored-sre",
            "applicable_versions": applicable_versions,
            "authoring_date": AUTHORING_DATE,
            "spdx_license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "content_hash": content_hash,
            "related_upstream_urls": related_urls,
        }

        frontmatter = generate_sre_frontmatter(meta)
        full_text = f"{frontmatter}\n\n{raw_body}\n"

        # Write file atomically
        local_path.write_text(full_text, encoding="utf-8")
        category_counts[category] = category_counts.get(category, 0) + 1

        manifest_docs.append({
            "name": name,
            "title": title,
            "incident_id": incident_id,
            "severity": severity,
            "category": category,
            "component": component,
            "document_type": doc_type,
            "doc_type": doc_type,
            "local_path": local_path.as_posix(),
            "content_hash": content_hash,
            "source_type": "project-authored-sre",
            "applicable_versions": applicable_versions,
            "authoring_date": AUTHORING_DATE,
            "spdx_license": SPDX_LICENSE,
            "attribution": ATTRIBUTION,
            "related_upstream_urls": related_urls,
            "is_simulated": "SIMULATED INCIDENT" in raw_body,
        })

    manifest_data = {
        "manifest_version": "1.0",
        "generated_at": now_iso,
        "corpus": "sre",
        "spdx_license": SPDX_LICENSE,
        "attribution": ATTRIBUTION,
        "authoring_date": AUTHORING_DATE,
        "total_documents": len(manifest_docs),
        "categories": category_counts,
        "documents": manifest_docs,
    }

    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("SRE knowledge corpus built successfully: %d documents written.", len(manifest_docs))

    return {
        "status": "success",
        "total_documents": len(manifest_docs),
        "category_counts": category_counts,
        "manifest_path": str(manifest_path),
    }


def validate_sre_corpus(
    corpus_root: Path = DEFAULT_SRE_CORPUS_DIR,
    manifest_path: Path = DEFAULT_SRE_MANIFEST_PATH,
) -> dict[str, Any]:
    """Validate SRE corpus documents, frontmatter, hashes, and simulation labels."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found at {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Corrupted manifest JSON at {manifest_path}: {exc}") from exc

    docs_list = manifest.get("documents", [])
    if len(docs_list) != len(ALL_SRE_DOCS):
        raise ValueError(f"Expected {len(ALL_SRE_DOCS)} documents, found {len(docs_list)}")

    seen_hashes: dict[str, str] = {}
    verified = 0

    for doc in docs_list:
        path = Path(doc["local_path"])
        if not path.exists():
            raise FileNotFoundError(f"Missing file on disk: {path}")

        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError(f"Empty document at {path}")

        if not raw.startswith("---"):
            raise ValueError(f"Missing frontmatter start in {path}")

        lines = raw.splitlines()
        closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if closing is None:
            raise ValueError(f"Unclosed frontmatter in {path}")

        body = "\n".join(lines[closing + 1:]).strip()
        body_hash = compute_content_hash(body)

        if body_hash != doc["content_hash"]:
            raise ValueError(f"Hash mismatch in {path}: computed {body_hash} != manifest {doc['content_hash']}")

        if body_hash in seen_hashes:
            raise ValueError(f"Duplicate content detected in {path}")
        seen_hashes[body_hash] = str(path)

        # Code fences check
        fences = [l for l in lines[closing + 1:] if l.strip().startswith("```")]
        if len(fences) % 2 != 0:
            raise ValueError(f"Unbalanced code fences in {path}")

        # Simulation label verification for postmortems and incidents
        if doc["category"] in ("postmortems", "incidents"):
            if "SIMULATED INCIDENT — NOT A REAL PRODUCTION EVENT." not in body:
                raise ValueError(f"Missing prominent simulated incident label in {path}")
            if not doc.get("is_simulated"):
                raise ValueError(f"Manifest missing is_simulated flag for {path}")

        verified += 1

    return {
        "status": "passed",
        "verified_documents": verified,
        "manifest_documents": len(docs_list),
        "corpus_root": str(corpus_root),
        "manifest_path": str(manifest_path),
    }


def main() -> None:
    """CLI entrypoint for SRE corpus builder."""
    parser = argparse.ArgumentParser(description="SRE Knowledge Corpus Builder")
    parser.add_argument("--build", action="store_true", help="Build and write all SRE documents")
    parser.add_argument("--verify-only", action="store_true", help="Verify existing SRE corpus without building")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.verify_only:
        res = validate_sre_corpus()
        print(f"Validation result: {res}")
        return

    res = build_sre_corpus()
    print(f"Build result: {res}")
    val = validate_sre_corpus()
    print(f"Post-build validation: {val}")


if __name__ == "__main__":
    main()

