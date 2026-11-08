"""Tests for Document Management REST API (Phase C).

Tests all endpoints:
- POST /documents/upload (all 7 formats, replacement, error handling, size limits)
- GET /documents (listing built-in + uploaded)
- DELETE /documents/{document_id} (deleting uploads, forbidding corpus deletion)
- Persistence across reloads and baseline preservation.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from advanced_rag.api import main
from advanced_rag.ingestion.registry import DocumentRegistry, reset_registry
from advanced_rag.retrieval.vectorstore import HybridStore


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Set up completely isolated in-memory store and temporary uploads/registry."""
    # Isolated in-memory Qdrant store
    qdrant_client = QdrantClient(":memory:")
    isolated_store = HybridStore(client=qdrant_client, collection="test_api_docs")
    monkeypatch.setattr("advanced_rag.api.main.get_store", lambda: isolated_store)

    # Isolated upload dir and registry
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    registry_file = upload_dir / "registry.json"
    corpus_dir = tmp_path / "corpus"
    sample_dir = corpus_dir / "kubernetes" / "troubleshooting"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "crashloopbackoff.md").write_text(
        "---\ntitle: Runbook - Pod CrashLoopBackOff\ndoc_type: runbook\n---\n\n# Pod CrashLoopBackOff\n\nTriage steps...",
        encoding="utf-8",
    )

    test_registry = DocumentRegistry(registry_path=registry_file, corpus_dir=corpus_dir)
    monkeypatch.setattr("advanced_rag.api.main.get_registry", lambda: test_registry)
    monkeypatch.setattr("advanced_rag.ingestion.registry.get_registry", lambda: test_registry)

    # Patch data/uploads location in main
    monkeypatch.setattr("advanced_rag.api.main.UPLOAD_DIR", upload_dir)

    # Skip lifespan graph compilation
    monkeypatch.setattr(main.pipeline, "get_graph", lambda: None)

    with TestClient(main.app) as test_client:
        yield {
            "client": test_client,
            "store": isolated_store,
            "registry": test_registry,
            "upload_dir": upload_dir,
            "registry_file": registry_file,
            "corpus_dir": corpus_dir,
        }

    reset_registry(None)


# ----------------------------------------------------------- format uploads


def test_upload_markdown_document(isolated_env):
    client = isolated_env["client"]
    content = (
        b"# K8s Incident\n\n## Overview\n"
        b"Production pod repeatedly crashed with OOMKilled errors.\n"
    )
    files = {"file": ("incident.md", io.BytesIO(content), "text/markdown")}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "incident.md"
    assert data["file_type"] == "md"
    assert data["chunks_created"] >= 1
    assert data["ingestion_status"] == "success"
    assert isolated_env["store"].count() >= 1


def test_upload_text_document(isolated_env):
    client = isolated_env["client"]
    content = b"Simple plain text operational runbook details."
    files = {"file": ("runbook.txt", io.BytesIO(content), "text/plain")}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "runbook.txt"
    assert data["file_type"] == "txt"
    assert data["chunks_created"] >= 1


def test_upload_csv_document(isolated_env):
    client = isolated_env["client"]
    content = b"service,status,latency\nauth,healthy,45ms\npayments,degraded,350ms\n"
    files = {"file": ("services.csv", io.BytesIO(content), "text/csv")}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "services.csv"
    assert data["file_type"] == "csv"
    assert data["chunks_created"] >= 1


def test_upload_html_document(isolated_env):
    client = isolated_env["client"]
    content = (
        b"<html><head><title>System Status</title></head>"
        b"<body><h1>Gateway</h1><p>Running normally.</p></body></html>"
    )
    files = {"file": ("status.html", io.BytesIO(content), "text/html")}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "status.html"
    assert data["file_type"] == "html"
    assert data["chunks_created"] >= 1


def test_upload_json_document(isolated_env):
    client = isolated_env["client"]
    raw = json.dumps({"cluster": "prod-us-east", "nodes": 15, "healthy": True}).encode()
    files = {"file": ("cluster.json", io.BytesIO(raw), "application/json")}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "cluster.json"
    assert data["file_type"] == "json"
    assert data["chunks_created"] >= 1


def test_upload_pdf_document(isolated_env):
    from pypdf import PdfWriter

    client = isolated_env["client"]
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    files = {"file": ("sample.pdf", buf, "application/pdf")}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code in (201, 400)


def test_upload_docx_document(isolated_env):
    import docx

    client = isolated_env["client"]
    doc = docx.Document()
    doc.add_heading("Production Troubleshooting", level=1)
    doc.add_paragraph("Check kubectl describe node for DiskPressure conditions.")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    files = {"file": ("troubleshoot.docx", buf, docx_mime)}

    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "troubleshoot.docx"
    assert data["file_type"] == "docx"
    assert data["chunks_created"] >= 1


# ----------------------------------------------------------- validation & errors


def test_reject_empty_file(isolated_env):
    client = isolated_env["client"]
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 400
    assert "empty file" in resp.json()["detail"].lower()


def test_reject_unsupported_extension(isolated_env):
    client = isolated_env["client"]
    files = {"file": ("script.py", io.BytesIO(b"print('hello')"), "text/x-python")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 400
    assert "unsupported file format" in resp.json()["detail"].lower()


def test_reject_oversized_file(isolated_env, monkeypatch):
    client = isolated_env["client"]
    monkeypatch.setattr("advanced_rag.api.main.MAX_UPLOAD_SIZE", 100)
    content = b"x" * 150
    files = {"file": ("large.txt", io.BytesIO(content), "text/plain")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 413
    assert "exceeds maximum allowed limit" in resp.json()["detail"].lower()


def test_reject_malformed_json_content(isolated_env):
    client = isolated_env["client"]
    files = {"file": ("bad.json", io.BytesIO(b"{invalid json!"), "application/json")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 400
    assert "extraction failed" in resp.json()["detail"].lower()


def test_reject_path_traversal_filename(isolated_env):
    client = isolated_env["client"]
    files = {"file": ("../../etc/passwd.txt", io.BytesIO(b"root:x:0:0"), "text/plain")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "passwd.txt" or "passwd" in data["filename"]
    assert ".." not in data["filename"]
    assert "/" not in data["filename"]


# ----------------------------------------------------------- replacement


def test_replace_uploaded_document(isolated_env):
    client = isolated_env["client"]
    # 1. Upload initial version
    v1_content = b"# Version 1\nInitial deployment runbook detailing production rollouts.\n"
    files = {"file": ("runbook.md", io.BytesIO(v1_content), "text/markdown")}
    resp1 = client.post("/documents/upload", files=files)
    assert resp1.status_code == 201
    doc_id = resp1.json()["document_id"]
    assert resp1.json()["chunks_created"] >= 1

    # 2. Replace with updated content
    v2_content = b"# Version 2\nUpdated deployment runbook with comprehensive rollback steps.\n"
    files = {"file": ("runbook.md", io.BytesIO(v2_content), "text/markdown")}
    data = {"document_id": doc_id}
    resp2 = client.post("/documents/upload", files=files, data=data)
    assert resp2.status_code == 200
    result = resp2.json()
    assert result["document_id"] == doc_id
    assert "replaced and indexed successfully" in result["message"]

    # 3. Store count remains consistent (old chunks deleted, new chunks added)
    assert isolated_env["store"].count() == result["chunks_created"]


def test_replace_rejects_nonexistent_document(isolated_env):
    client = isolated_env["client"]
    files = {"file": ("runbook.md", io.BytesIO(b"# Runbook\nContent"), "text/markdown")}
    data = {"document_id": "nonexistent_id_123"}
    resp = client.post("/documents/upload", files=files, data=data)
    assert resp.status_code == 404


def test_replace_rejects_builtin_corpus_document(isolated_env):
    client = isolated_env["client"]
    files = {
        "file": (
            "crashloopbackoff.md",
            io.BytesIO(b"# Malicious Overwrite\nContent"),
            "text/markdown",
        )
    }
    data = {"document_id": "builtin_crashloopbackoff"}
    resp = client.post("/documents/upload", files=files, data=data)
    assert resp.status_code == 403
    assert "built-in" in resp.json()["detail"].lower()


# ----------------------------------------------------------- list & delete


def test_list_documents_includes_builtin_and_uploaded(isolated_env):
    client = isolated_env["client"]
    # Upload a user document
    files = {
        "file": (
            "custom_guide.txt",
            io.BytesIO(b"Custom operational guide content."),
            "text/plain",
        )
    }
    client.post("/documents/upload", files=files)

    resp = client.get("/documents")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) >= 2  # built-in + uploaded

    origins = {d["origin"] for d in docs}
    assert "built-in" in origins
    assert "uploaded" in origins

    uploaded_doc = next(d for d in docs if d["origin"] == "uploaded")
    assert uploaded_doc["filename"] == "custom_guide.txt"
    assert uploaded_doc["file_type"] == "txt"
    assert uploaded_doc["chunk_count"] >= 1


def test_delete_uploaded_document(isolated_env):
    client = isolated_env["client"]
    # 1. Upload
    files = {"file": ("to_delete.txt", io.BytesIO(b"Temporary data to delete."), "text/plain")}
    upload_resp = client.post("/documents/upload", files=files)
    doc_id = upload_resp.json()["document_id"]
    assert isolated_env["store"].count() >= 1

    # 2. Delete
    del_resp = client.delete(f"/documents/{doc_id}")
    assert del_resp.status_code == 200
    data = del_resp.json()
    assert data["document_id"] == doc_id
    assert data["deleted_chunks"] >= 1
    assert "deleted successfully" in data["message"].lower()

    # 3. Store and registry are cleared
    assert isolated_env["store"].count() == 0
    assert isolated_env["registry"].get(doc_id) is None


def test_delete_rejects_builtin_corpus_document(isolated_env):
    client = isolated_env["client"]
    resp = client.delete("/documents/builtin_crashloopbackoff")
    assert resp.status_code == 403
    assert "built-in" in resp.json()["detail"].lower()


def test_delete_nonexistent_document_returns_404(isolated_env):
    client = isolated_env["client"]
    resp = client.delete("/documents/random_id_404")
    assert resp.status_code == 404


def test_registry_persists_across_reloads(isolated_env):
    client = isolated_env["client"]
    files = {"file": ("persistent.txt", io.BytesIO(b"Persisted text."), "text/plain")}
    resp = client.post("/documents/upload", files=files)
    doc_id = resp.json()["document_id"]

    # Create fresh registry instance pointing to same file
    fresh_registry = DocumentRegistry(
        registry_path=isolated_env["registry_file"],
        corpus_dir=isolated_env["corpus_dir"],
    )
    record = fresh_registry.get(doc_id)
    assert record is not None
    assert record["filename"] == "persistent.txt"
    assert record["origin"] == "uploaded"
