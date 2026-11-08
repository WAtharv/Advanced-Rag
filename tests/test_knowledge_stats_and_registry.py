"""Tests for Knowledge Base Statistics (Task 42) and Persistent Document Registry (Task 43).

Covers all 18 verification requirements:
1. New document registration.
2. Duplicate registration (idempotent).
3. Metadata updates.
4. Successful indexing status.
5. Failed indexing status.
6. Modified document synchronization.
7. Deletion status.
8. Registry and Qdrant consistency.
9. Statistics accuracy.
10. Category and format aggregation.
11. Pagination and filtering.
12. PostgreSQL integration where available / dialect verification.
13. SQLite fallback.
14. Transaction rollback.
15. Restart persistence.
16. Missing metadata reporting.
17. API response validation.
18. Existing ingestion compatibility.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from advanced_rag.api.knowledge import get_persistent_registry
from advanced_rag.api.main import app
from advanced_rag.config import get_settings
from advanced_rag.db.models import Base, DocumentMetadataRecord
from advanced_rag.db.session import get_db_session, init_db
from advanced_rag.ingestion.extractors.base import Document
from advanced_rag.ingestion.loader import chunk_corpus
from advanced_rag.ingestion.persistent_registry import PersistentDocumentRegistry
from advanced_rag.ingestion.stats import KnowledgeBaseStatsService
from advanced_rag.ingestion.sync import CorpusSynchronizer
from advanced_rag.retrieval.vectorstore import HybridStore


@pytest.fixture
def temp_engine(tmp_path):
    """Create an isolated temporary SQLite database engine."""
    db_file = tmp_path / f"test_reg_{uuid.uuid4().hex[:8]}.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def temp_registry(temp_engine):
    """Create a persistent registry instance bound to the isolated temporary engine."""
    return PersistentDocumentRegistry(engine=temp_engine)


@pytest.fixture
def temp_store():
    """Create an isolated temporary Qdrant collection for safe testing."""
    test_collection = f"test_stats_{uuid.uuid4().hex[:10]}"
    settings = get_settings()
    store = HybridStore(settings=settings, collection=test_collection)
    store.ensure_collection(recreate=True)
    try:
        yield store
    finally:
        try:
            if store.client.collection_exists(test_collection):
                store.client.delete_collection(test_collection)
        except Exception:
            pass
        store.close()


# -----------------------------------------------------------------------------
# 1. New document registration
# -----------------------------------------------------------------------------
def test_new_document_registration(temp_registry):
    record = {
        "document_id": "doc_001",
        "source": "kubernetes/storage/pvc.md",
        "title": "Persistent Volume Claims",
        "file_type": "md",
        "content_hash": "a1b2c3d4",
        "category": "storage",
        "component": "csi",
        "doc_type": "runbook",
        "status": "pending",
        "chunk_count": 0,
    }
    result = temp_registry.register(record)
    assert result["document_id"] == "doc_001"
    assert result["title"] == "Persistent Volume Claims"
    assert result["document_status"] == "pending"
    assert result["created_at"] is not None

    fetched = temp_registry.get("doc_001")
    assert fetched is not None
    assert fetched["source"] == "kubernetes/storage/pvc.md"


# -----------------------------------------------------------------------------
# 2. Duplicate registration (idempotent)
# -----------------------------------------------------------------------------
def test_duplicate_registration_is_idempotent(temp_registry):
    record = {
        "document_id": "doc_dup",
        "source": "helm/install.md",
        "title": "Initial Helm Install",
        "file_type": "md",
        "content_hash": "hash_v1",
        "status": "pending",
    }
    temp_registry.register(record)

    # Re-register with updated title
    record_updated = dict(record)
    record_updated["title"] = "Updated Helm Install Guide"
    temp_registry.register(record_updated)

    docs, total = temp_registry.list_documents(limit=10)
    assert total == 1
    assert docs[0]["title"] == "Updated Helm Install Guide"


# -----------------------------------------------------------------------------
# 3. Metadata updates
# -----------------------------------------------------------------------------
def test_metadata_updates(temp_registry):
    temp_registry.register({
        "document_id": "meta_doc",
        "source": "prometheus/alerts.md",
        "title": "Alerts Guide",
        "status": "pending",
    })

    # Update rich metadata fields
    temp_registry.register({
        "document_id": "meta_doc",
        "source": "prometheus/alerts.md",
        "source_url": "https://prometheus.io/docs/alerting/",
        "source_version": "2.54.0",
        "spdx_license": "Apache-2.0",
        "attribution": "Prometheus Authors",
        "category": "alerting",
    })

    doc = temp_registry.get("meta_doc")
    assert doc["source_url"] == "https://prometheus.io/docs/alerting/"
    assert doc["source_version"] == "2.54.0"
    assert doc["spdx_license"] == "Apache-2.0"
    assert doc["category"] == "alerting"


# -----------------------------------------------------------------------------
# 4. Successful indexing status
# -----------------------------------------------------------------------------
def test_successful_indexing_status(temp_registry):
    temp_registry.register({
        "document_id": "idx_success",
        "source": "sre/oom.md",
        "title": "OOM Remediation",
        "status": "indexing",
    })

    ok = temp_registry.record_indexing_success("idx_success", chunk_count=8, content_hash="hash_final")
    assert ok is True

    doc = temp_registry.get("idx_success")
    assert doc["document_status"] == "indexed"
    assert doc["indexed_chunk_count"] == 8
    assert doc["content_hash"] == "hash_final"
    assert doc["last_indexed_at"] is not None
    assert doc["last_error"] is None


# -----------------------------------------------------------------------------
# 5. Failed indexing status
# -----------------------------------------------------------------------------
def test_failed_indexing_status(temp_registry):
    temp_registry.register({
        "document_id": "idx_fail",
        "source": "sre/broken.md",
        "title": "Broken Runbook",
        "status": "indexing",
    })

    ok = temp_registry.record_indexing_failure("idx_fail", error="Embedding server connection refused")
    assert ok is True

    doc = temp_registry.get("idx_fail")
    assert doc["document_status"] == "failed"
    assert "connection refused" in doc["last_error"]


# -----------------------------------------------------------------------------
# 6. Modified document synchronization
# -----------------------------------------------------------------------------
def test_modified_document_synchronization(temp_registry, temp_store):
    doc = Document(
        source="sync/mod.md",
        title="Mod Test",
        body="# Step 1\nInitial instructions for service configuration.",
        doc_type="runbook",
    )
    chunks1 = chunk_corpus([doc])
    temp_store.upsert(chunks1)
    temp_registry.register({
        "document_id": "sync_mod_doc",
        "source": "sync/mod.md",
        "title": "Mod Test",
        "content_hash": "v1_hash",
        "status": "indexed",
        "chunk_count": len(chunks1),
        "collection_name": temp_store.collection,
    })

    # Update on disk
    doc_updated = Document(
        source="sync/mod.md",
        title="Mod Test",
        body="# Step 1\nInitial instructions.\n\n# Step 2\nAdditional instructions.",
        doc_type="runbook",
    )
    chunks2 = chunk_corpus([doc_updated])
    temp_store.replace_document(chunks2, source="sync/mod.md")
    temp_registry.record_indexing_success("sync_mod_doc", chunk_count=len(chunks2), content_hash="v2_hash")

    res = temp_registry.get("sync_mod_doc")
    assert res["content_hash"] == "v2_hash"
    assert res["indexed_chunk_count"] == len(chunks2)
    assert temp_store.count() == len(chunks2)


# -----------------------------------------------------------------------------
# 7. Deletion status
# -----------------------------------------------------------------------------
def test_deletion_status(temp_registry):
    temp_registry.register({
        "document_id": "to_delete",
        "source": "deprecated.md",
        "title": "Deprecated Procedure",
        "status": "indexed",
        "chunk_count": 5,
    })

    ok = temp_registry.delete("to_delete")
    assert ok is True

    # Active documents query excludes deleted by default
    active_docs, total_active = temp_registry.list_documents(include_deleted=False)
    assert total_active == 0

    # Including deleted shows the marked record
    all_docs, total_all = temp_registry.list_documents(include_deleted=True)
    assert total_all == 1
    assert all_docs[0]["document_status"] == "deleted"
    assert all_docs[0]["indexed_chunk_count"] == 0


# -----------------------------------------------------------------------------
# 8. Registry and Qdrant consistency
# -----------------------------------------------------------------------------
def test_registry_and_qdrant_consistency(temp_registry, temp_store):
    # 1. Consistent doc: present in both with matching chunks
    doc1 = Document(
        source="shared/doc1.md",
        title="Doc 1",
        body="# Shared Doc 1\nOperational procedure with sufficient length to create chunk.",
        doc_type="runbook",
    )
    c1 = chunk_corpus([doc1])
    temp_store.upsert(c1)
    temp_registry.register({
        "document_id": "doc1_id",
        "source": "shared/doc1.md",
        "title": "Doc 1",
        "status": "indexed",
        "chunk_count": len(c1),
        "collection_name": temp_store.collection,
    })

    # 2. Registry-only doc: present in DB but not indexed in Qdrant
    temp_registry.register({
        "document_id": "reg_only_id",
        "source": "db/only.md",
        "title": "DB Only",
        "status": "pending",
        "chunk_count": 0,
        "collection_name": temp_store.collection,
    })

    # 3. Qdrant-only doc: present in Qdrant but not registered
    doc3 = Document(
        source="qdrant/only.md",
        title="Qdrant Only",
        body="# Qdrant Only Doc\nThis document exists only inside vector index points.",
        doc_type="runbook",
    )
    temp_store.upsert(chunk_corpus([doc3]))

    cmp = temp_registry.compare_with_qdrant(temp_store)
    assert cmp["consistent_count"] == 1
    assert "shared/doc1.md" in cmp["consistent"]
    assert cmp["registry_only_count"] == 1
    assert "db/only.md" in cmp["registry_only"]
    assert cmp["qdrant_only_count"] == 1
    assert "qdrant/only.md" in cmp["qdrant_only"]


# -----------------------------------------------------------------------------
# 9. Statistics accuracy
# -----------------------------------------------------------------------------
def test_statistics_accuracy(temp_registry):
    temp_registry.register({
        "document_id": "d1",
        "source": "k8s/node.md",
        "category": "kubernetes",
        "component": "kubelet",
        "file_type": "md",
        "source_version": "1.37.0",
        "source_repository": "https://github.com/kubernetes/website",
        "source_url": "https://k8s.io/docs/node",
        "spdx_license": "CC-BY-4.0",
        "attribution": "Linux Foundation",
    })
    temp_registry.register({
        "document_id": "d2",
        "source": "helm/chart.md",
        "category": "helm",
        "component": "cli",
        "file_type": "md",
        "source_version": "4.3.1",
        "source_repository": "https://github.com/helm/helm-www",
        "source_url": "https://helm.sh/docs",
        "spdx_license": "CC-BY-4.0",
        "attribution": "Helm Authors",
    })

    svc = KnowledgeBaseStatsService(registry=temp_registry)
    inv = svc.get_inventory_stats()
    assert inv["total_documents"] == 2
    assert inv["by_category"]["kubernetes"] == 1
    assert inv["by_category"]["helm"] == 1

    src = svc.get_source_info_stats()
    assert src["source_url_percentage"] == 100.0
    assert src["license_completeness_percentage"] == 100.0


# -----------------------------------------------------------------------------
# 10. Category and format aggregation
# -----------------------------------------------------------------------------
def test_category_and_format_aggregation(temp_registry):
    formats = ["md", "pdf", "docx", "json", "csv"]
    for i, fmt in enumerate(formats):
        temp_registry.register({
            "document_id": f"fmt_{i}",
            "source": f"cat_a/file_{i}.{fmt}",
            "category": "cat_a" if i < 3 else "cat_b",
            "file_type": fmt,
        })

    svc = KnowledgeBaseStatsService(registry=temp_registry)
    inv = svc.get_inventory_stats()
    assert inv["by_category"]["cat_a"] == 3
    assert inv["by_category"]["cat_b"] == 2
    for fmt in formats:
        assert inv["by_file_format"][fmt] == 1


# -----------------------------------------------------------------------------
# 11. Pagination and filtering
# -----------------------------------------------------------------------------
def test_pagination_and_filtering(temp_registry):
    for i in range(15):
        temp_registry.register({
            "document_id": f"doc_page_{i:02d}",
            "source": f"batch/doc_{i:02d}.md",
            "category": "even" if i % 2 == 0 else "odd",
            "status": "indexed" if i < 10 else "pending",
        })

    # Page 1
    docs_p1, total = temp_registry.list_documents(limit=5, offset=0)
    assert len(docs_p1) == 5
    assert total == 15

    # Page 2
    docs_p2, _ = temp_registry.list_documents(limit=5, offset=5)
    assert len(docs_p2) == 5
    assert docs_p1[0]["document_id"] != docs_p2[0]["document_id"]

    # Filter by category
    even_docs, even_total = temp_registry.list_documents(category="even")
    assert even_total == 8

    # Filter by status
    pending_docs, pending_total = temp_registry.list_documents(status="pending")
    assert pending_total == 5


# -----------------------------------------------------------------------------
# 12. PostgreSQL integration where available / dialect verification
# -----------------------------------------------------------------------------
def test_postgresql_integration_where_available():
    """Verify PostgreSQL compatibility and DDL generation."""
    settings = get_settings()
    if settings.postgres_dsn:
        eng = create_engine(settings.sql_url, pool_pre_ping=True)
        init_db(eng)
        with get_db_session(eng) as session:
            count = session.scalar(select(Base.metadata.tables["document_metadata"].name))
            assert count == "document_metadata"
        eng.dispose()
    else:
        # Verify that models compile cleanly against PostgreSQL dialect
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        create_ddl = str(CreateTable(DocumentMetadataRecord.__table__).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE document_metadata" in create_ddl
        assert "VARCHAR" in create_ddl or "TEXT" in create_ddl
        assert "document_id" in create_ddl


# -----------------------------------------------------------------------------
# 13. SQLite fallback
# -----------------------------------------------------------------------------
def test_sqlite_fallback(tmp_path):
    db_file = tmp_path / "sqlite_test.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    init_db(engine)

    reg = PersistentDocumentRegistry(engine=engine)
    reg.register({"document_id": "sqlite_doc", "source": "test.md", "title": "SQLite Test"})
    doc = reg.get("sqlite_doc")
    assert doc is not None
    assert doc["title"] == "SQLite Test"
    engine.dispose()


# -----------------------------------------------------------------------------
# 14. Transaction rollback on error
# -----------------------------------------------------------------------------
def test_transaction_rollback(temp_engine):
    with pytest.raises(RuntimeError, match="Simulated crash during insert"):
        with get_db_session(temp_engine) as session:
            session.add(
                DocumentMetadataRecord(
                    document_id="rollback_doc",
                    source="rollback.md",
                    title="Rollback Test",
                )
            )
            raise RuntimeError("Simulated crash during insert")

    # Verify document was not committed
    with get_db_session(temp_engine) as session:
        stmt = select(DocumentMetadataRecord).where(DocumentMetadataRecord.document_id == "rollback_doc")
        assert session.scalars(stmt).first() is None


# -----------------------------------------------------------------------------
# 15. Restart persistence
# -----------------------------------------------------------------------------
def test_restart_persistence(tmp_path):
    db_path = tmp_path / "restart.db"
    url = f"sqlite+pysqlite:///{db_path.as_posix()}"

    # First session
    eng1 = create_engine(url, connect_args={"check_same_thread": False})
    reg1 = PersistentDocumentRegistry(engine=eng1)
    reg1.register({"document_id": "persist_1", "source": "persist.md", "title": "Persistent Title"})
    eng1.dispose()

    # Restart session with a fresh engine instance bound to the same file
    eng2 = create_engine(url, connect_args={"check_same_thread": False})
    reg2 = PersistentDocumentRegistry(engine=eng2)
    doc = reg2.get("persist_1")
    assert doc is not None
    assert doc["title"] == "Persistent Title"
    eng2.dispose()


# -----------------------------------------------------------------------------
# 16. Missing metadata reporting
# -----------------------------------------------------------------------------
def test_missing_metadata_reporting(temp_registry):
    temp_registry.register({
        "document_id": "bare_doc",
        "source": "bare.md",
        "title": "Bare Doc",
        # Missing category, component, source_url, license
    })

    svc = KnowledgeBaseStatsService(registry=temp_registry)
    src = svc.get_source_info_stats()
    assert src["total_documents"] == 1
    assert src["documents_missing_metadata"] == 1
    assert src["source_url_count"] == 0
    assert src["license_complete_count"] == 0


# -----------------------------------------------------------------------------
# 17. API response validation
# -----------------------------------------------------------------------------
def test_api_response_validation(temp_registry):
    app.dependency_overrides[get_persistent_registry] = lambda: temp_registry

    # Populate sample data
    temp_registry.register({
        "document_id": "api_doc_1",
        "source": "api/test.md",
        "title": "API Test Document",
        "category": "api_test",
        "component": "gateway",
        "file_type": "md",
        "status": "indexed",
        "chunk_count": 3,
    })

    try:
        with TestClient(app) as client:
            # GET /api/knowledge/stats
            res_stats = client.get("/api/knowledge/stats")
            assert res_stats.status_code == 200
            stats_data = res_stats.json()
            assert "inventory" in stats_data
            assert "indexing" in stats_data
            assert "ingestion_status" in stats_data
            assert "source_information" in stats_data

            # GET /api/knowledge/documents
            res_docs = client.get("/api/knowledge/documents?category=api_test&page=1&page_size=10")
            assert res_docs.status_code == 200
            docs_data = res_docs.json()
            assert docs_data["total"] >= 1
            assert any(item["document_id"] == "api_doc_1" for item in docs_data["items"])

            # GET /api/knowledge/documents/{document_id}
            res_single = client.get("/api/knowledge/documents/api_doc_1")
            assert res_single.status_code == 200
            assert res_single.json()["title"] == "API Test Document"

            # GET /api/knowledge/documents/nonexistent
            res_404 = client.get("/api/knowledge/documents/nonexistent_xyz")
            assert res_404.status_code == 404

            # GET /api/knowledge/categories
            res_cats = client.get("/api/knowledge/categories")
            assert res_cats.status_code == 200
            assert any(c["category"] == "api_test" for c in res_cats.json())

            # GET /api/knowledge/ingestion/status
            res_ing = client.get("/api/knowledge/ingestion/status")
            assert res_ing.status_code == 200

            # Invalid pagination validation (422)
            res_invalid = client.get("/api/knowledge/documents?page=0")
            assert res_invalid.status_code == 422
    finally:
        app.dependency_overrides.clear()


# -----------------------------------------------------------------------------
# 18. Existing ingestion compatibility
# -----------------------------------------------------------------------------
def test_existing_ingestion_compatibility(tmp_path, temp_store):
    doc_path = tmp_path / "compat.md"
    doc_path.write_text(
        "# Compatibility Test\n\nEnsure synchronization updates vector store and persistent registry in unison.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan = syncer.create_plan(tmp_path, store=temp_store)
    res = syncer.execute_sync(plan, store=temp_store)
    assert res["upserted_chunks"] == 1
    assert temp_store.count() == 1

    # Verify persistent registry was updated
    reg = PersistentDocumentRegistry()
    doc_record = reg.get_by_source("compat.md", collection_name=temp_store.collection)
    if not doc_record:
        doc_record = reg.get_by_source(f"{tmp_path.name}/compat.md")

    assert doc_record is not None or reg.get(plan.new_documents[0].document_id) is not None
