"""Tests for document lifecycle, safe Qdrant cleanup, and corpus synchronization.

Covers all 17 requirements of Stage 3B:
1. Delete document with 1 chunk.
2. Delete document with multiple chunks.
3. Delete document with no matching points (returns 0 deleted, found=False).
4. Repeated deletion (idempotent).
5. Delete document when another document has identical content (collision test).
6. Synchronize a new document.
7. Synchronize a modified document (old chunks removed, new chunks present).
8. Detect an absent/deleted source document.
9. Preserve unchanged documents.
10. Prune stale documents only with explicit approval (--prune + --confirm).
11. Re-run synchronization without additional points (idempotent).
12. Recovery/safety: if ingestion of replacement fails, old chunks remain.
13. Protection of active collection (sre_copilot_v1 safeguards).
14. Protection of baseline collections (k8s_ops, k8s_ops_bge_small).
15. Paths containing spaces.
16. Nested corpus directory discovery.
17. Metadata and citations preserved after synchronization.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from advanced_rag.config import get_settings
from advanced_rag.ingestion import cli
from advanced_rag.ingestion.extractors.base import Document
from advanced_rag.ingestion.loader import chunk_corpus
from advanced_rag.ingestion.sync import (
    CorpusSynchronizer,
    SyncStatus,
)
from advanced_rag.retrieval.vectorstore import HybridStore


@pytest.fixture
def temp_store():
    """Create an isolated temporary Qdrant collection for safe testing."""
    test_collection = f"test_sync_{uuid.uuid4().hex[:10]}"
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
# 1. Delete document with 1 chunk
# -----------------------------------------------------------------------------
def test_delete_document_single_chunk(temp_store):
    doc = Document(
        source="test/single.md",
        title="Single Chunk Doc",
        body="A comprehensive runbook describing the exact single-step cluster recovery procedure for failed workers.",
        doc_type="runbook",
    )
    chunks = chunk_corpus([doc])
    assert len(chunks) == 1
    temp_store.upsert(chunks)
    assert temp_store.count() == 1

    res = temp_store.delete_document("test/single.md")
    assert res["deleted_chunks"] == 1
    assert res["matched_points"] == 1
    assert res["found"] is True
    assert temp_store.count() == 0


# -----------------------------------------------------------------------------
# 2. Delete document with multiple chunks
# -----------------------------------------------------------------------------
def test_delete_document_multiple_chunks(temp_store):
    body = (
        "# Section 1: Kubelet Health\n\nFirst detailed operational instruction checking kubelet status and logs on node.\n\n"
        "# Section 2: Containerd Runtime\n\nSecond detailed operational instruction verifying containerd socket and cgroup.\n\n"
        "# Section 3: CNI Connectivity\n\nThird detailed operational instruction validating pod network interface and routing.\n\n"
        "# Section 4: Resolution Verification\n\nFourth detailed operational instruction confirming worker node is Ready."
    )
    doc = Document(
        source="test/multi.md",
        title="Multi Chunk Doc",
        body=body,
        doc_type="runbook",
    )
    chunks = chunk_corpus([doc])
    assert len(chunks) == 4
    temp_store.upsert(chunks)
    assert temp_store.count() == 4

    res = temp_store.delete_document("test/multi.md")
    assert res["deleted_chunks"] == 4
    assert res["found"] is True
    assert temp_store.count() == 0


# -----------------------------------------------------------------------------
# 3. Delete document with no matching points
# -----------------------------------------------------------------------------
def test_delete_document_nonexistent_returns_zero(temp_store):
    res = temp_store.delete_document("nonexistent/path/missing.md")
    assert res["deleted_chunks"] == 0
    assert res["matched_points"] == 0
    assert res["found"] is False


# -----------------------------------------------------------------------------
# 4. Repeated deletion (idempotent)
# -----------------------------------------------------------------------------
def test_delete_document_idempotent(temp_store):
    body = (
        "# Step 1: Initial Diagnosis\nVerify node conditions using kubectl describe node worker-1.\n\n"
        "# Step 2: Remediate Eviction\nClean disk space and restart kubelet daemon immediately."
    )
    doc = Document(
        source="test/idempotent.md",
        title="Idempotent Test",
        body=body,
        doc_type="runbook",
    )
    chunks = chunk_corpus([doc])
    assert len(chunks) == 2
    temp_store.upsert(chunks)
    assert temp_store.count() == 2

    # First deletion
    res1 = temp_store.delete_document("test/idempotent.md")
    assert res1["deleted_chunks"] == 2
    assert res1["found"] is True
    assert temp_store.count() == 0

    # Second repeated deletion
    res2 = temp_store.delete_document("test/idempotent.md")
    assert res2["deleted_chunks"] == 0
    assert res2["found"] is False
    assert temp_store.count() == 0


# -----------------------------------------------------------------------------
# 5. Collision safety: Delete doc when another doc has identical content
# -----------------------------------------------------------------------------
def test_delete_document_identical_content_collision(temp_store):
    shared_text = "Standard node drain procedure: execute kubectl drain node-1 --ignore-daemonsets --delete-emptydir-data."
    doc_a = Document(
        source="team_a/drain.md",
        title="Drain Node A",
        body=shared_text,
        doc_type="runbook",
    )
    doc_b = Document(
        source="team_b/drain.md",
        title="Drain Node B",
        body=shared_text,
        doc_type="runbook",
    )
    chunks_a = chunk_corpus([doc_a])
    chunks_b = chunk_corpus([doc_b])
    assert chunks_a[0].id != chunks_b[0].id, "Deterministic IDs must differ because source differs"

    temp_store.upsert(chunks_a + chunks_b)
    assert temp_store.count() == 2

    # Delete Document A
    res = temp_store.delete_document("team_a/drain.md")
    assert res["deleted_chunks"] == 1
    assert temp_store.count() == 1

    # Verify Document B is completely intact
    pids_b = temp_store.get_point_ids("team_b/drain.md")
    assert len(pids_b) == 1
    pids_a = temp_store.get_point_ids("team_a/drain.md")
    assert len(pids_a) == 0


# -----------------------------------------------------------------------------
# 6. Synchronize a new document
# -----------------------------------------------------------------------------
def test_synchronize_new_document(tmp_path, temp_store):
    doc_file = tmp_path / "new_procedure.md"
    doc_file.write_text(
        "# New Diagnostic\n\nRun journalctl -u kubelet -n 100 to diagnose kubelet failures across cluster nodes.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan = syncer.create_plan(tmp_path, store=temp_store)

    assert len(plan.new_documents) == 1
    assert plan.new_documents[0].status == SyncStatus.NEW
    assert len(plan.modified_documents) == 0
    assert len(plan.deleted_documents) == 0

    res = syncer.execute_sync(plan, store=temp_store)
    assert res["upserted_chunks"] == 1
    assert temp_store.count() == 1


# -----------------------------------------------------------------------------
# 7. Synchronize a modified document (old chunks removed, new chunks present)
# -----------------------------------------------------------------------------
def test_synchronize_modified_document(tmp_path, temp_store):
    doc_file = tmp_path / "scale.md"
    doc_file.write_text(
        "# Step 1: Scale Deployment\n\nExecute kubectl scale deployment web-api --replicas=3 in target namespace.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan1 = syncer.create_plan(tmp_path, store=temp_store)
    syncer.execute_sync(plan1, store=temp_store)
    assert temp_store.count() == 1

    # Modify document on disk by changing text and adding a section
    doc_file.write_text(
        "# Step 1: Scale Deployment\n\nExecute kubectl scale deployment web-api --replicas=5 in target namespace.\n\n"
        "# Step 2: Rollout Verification\n\nVerify pod rollout status with kubectl rollout status deployment/web-api.",
        encoding="utf-8",
    )

    plan2 = syncer.create_plan(tmp_path, store=temp_store)
    assert len(plan2.modified_documents) == 1
    assert plan2.modified_documents[0].status == SyncStatus.MODIFIED
    assert len(plan2.new_documents) == 0

    res2 = syncer.execute_sync(plan2, store=temp_store)
    assert res2["upserted_chunks"] == 2
    assert res2["deleted_obsolete_chunks"] == 1
    assert temp_store.count() == 2


# -----------------------------------------------------------------------------
# 8. Detect an absent/deleted source document
# -----------------------------------------------------------------------------
def test_detect_absent_deleted_source_document(tmp_path, temp_store):
    doc1 = tmp_path / "doc1.md"
    doc2 = tmp_path / "doc2.md"
    doc1.write_text(
        "# Doc 1\nPersistent instruction 1 detailing core application startup and healthcheck validation.",
        encoding="utf-8",
    )
    doc2.write_text(
        "# Doc 2\nInstruction to be deleted after testing stale point synchronization behavior.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan1 = syncer.create_plan(tmp_path, store=temp_store)
    syncer.execute_sync(plan1, store=temp_store)
    assert temp_store.count() == 2

    # Remove doc2 from disk
    doc2.unlink()

    plan2 = syncer.create_plan(tmp_path, store=temp_store)
    assert len(plan2.deleted_documents) == 1
    assert plan2.deleted_documents[0].status == SyncStatus.DELETED
    assert plan2.deleted_documents[0].source == "doc2.md"
    assert len(plan2.unchanged_documents) == 1
    assert plan2.unchanged_documents[0].source == "doc1.md"


# -----------------------------------------------------------------------------
# 9. Preserve unchanged documents
# -----------------------------------------------------------------------------
def test_preserve_unchanged_documents(tmp_path, temp_store):
    doc1 = tmp_path / "stable.md"
    doc1.write_text(
        "# Stable Operations\nCompletely unchanged operational content running across multiple cluster nodes.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan1 = syncer.create_plan(tmp_path, store=temp_store)
    syncer.execute_sync(plan1, store=temp_store)
    initial_count = temp_store.count()
    assert initial_count >= 1

    plan2 = syncer.create_plan(tmp_path, store=temp_store)
    assert len(plan2.unchanged_documents) == 1
    assert plan2.unchanged_documents[0].status == SyncStatus.UNCHANGED
    assert len(plan2.new_documents) == 0
    assert len(plan2.modified_documents) == 0

    res2 = syncer.execute_sync(plan2, store=temp_store)
    assert res2["upserted_chunks"] == 0
    assert temp_store.count() == initial_count


# -----------------------------------------------------------------------------
# 10. Prune stale documents only with explicit approval (--prune + --confirm)
# -----------------------------------------------------------------------------
def test_prune_stale_documents_requires_explicit_confirm(tmp_path, temp_store):
    doc1 = tmp_path / "active.md"
    doc2 = tmp_path / "stale.md"
    doc1.write_text(
        "# Active Service\nActive operational runbook for monitoring latency and traffic spikes.",
        encoding="utf-8",
    )
    doc2.write_text(
        "# Stale Service\nDeprecated operational runbook slated for removal during cluster upgrade.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan1 = syncer.create_plan(tmp_path, store=temp_store)
    syncer.execute_sync(plan1, store=temp_store)
    assert temp_store.count() == 2

    # Remove stale.md from disk
    doc2.unlink()
    plan2 = syncer.create_plan(tmp_path, store=temp_store)

    # 1. Prune=False: stale document is preserved in Qdrant
    res_no_prune = syncer.execute_sync(plan2, store=temp_store, prune=False)
    assert res_no_prune["pruned_stale_documents"] == 0
    assert temp_store.count() == 2

    # 2. Prune=True, confirm=False: raises error
    with pytest.raises(ValueError, match="requires explicit confirmation"):
        syncer.execute_sync(plan2, store=temp_store, prune=True, confirm=False)

    # 3. Prune=True, confirm=True: successfully pruned
    res_pruned = syncer.execute_sync(plan2, store=temp_store, prune=True, confirm=True)
    assert res_pruned["pruned_stale_documents"] == 1
    assert temp_store.count() == 1


# -----------------------------------------------------------------------------
# 11. Re-run synchronization without additional points (idempotent)
# -----------------------------------------------------------------------------
def test_rerun_synchronization_idempotent(tmp_path, temp_store):
    doc = tmp_path / "idemp.md"
    doc.write_text(
        "# Step 1: Pre-check\nRun initial diagnostics.\n\n# Step 2: Post-check\nVerify all pods in cluster are running.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan1 = syncer.create_plan(tmp_path, store=temp_store)
    syncer.execute_sync(plan1, store=temp_store)
    count_after_first = temp_store.count()
    assert count_after_first >= 1

    plan2 = syncer.create_plan(tmp_path, store=temp_store)
    res2 = syncer.execute_sync(plan2, store=temp_store)
    assert res2["upserted_chunks"] == 0
    assert res2["deleted_obsolete_chunks"] == 0
    assert temp_store.count() == count_after_first


# -----------------------------------------------------------------------------
# 12. Recovery/safety: if ingestion of replacement fails, old chunks remain
# -----------------------------------------------------------------------------
def test_recovery_safety_failed_upsert_preserves_old_chunks(temp_store, monkeypatch):
    doc = Document(
        source="test/fail_safe.md",
        title="Fail Safe Doc",
        body="# Recovery Steps\nOriginal critical runbook steps that must not be lost under any circumstances.",
        doc_type="runbook",
    )
    chunks = chunk_corpus([doc])
    assert len(chunks) == 1
    temp_store.upsert(chunks)
    assert temp_store.count() == 1
    initial_pids = temp_store.get_point_ids("test/fail_safe.md")

    # New updated chunks
    updated_doc = Document(
        source="test/fail_safe.md",
        title="Fail Safe Doc",
        body="# Recovery Steps\nUpdated steps intended to replace old configuration.",
        doc_type="runbook",
    )
    new_chunks = chunk_corpus([updated_doc])

    # Monkeypatch upsert to simulate a crash during embedding/upsert
    def failing_upsert(*args, **kwargs):
        raise RuntimeError("Simulated Qdrant / Embedder Network Failure")

    monkeypatch.setattr(temp_store, "upsert", failing_upsert)

    with pytest.raises(RuntimeError, match="Simulated Qdrant / Embedder Network Failure"):
        temp_store.replace_document(new_chunks, source="test/fail_safe.md")

    # Verify original chunks were NOT deleted
    remaining_pids = temp_store.get_point_ids("test/fail_safe.md")
    assert remaining_pids == initial_pids
    assert temp_store.count() == 1


# -----------------------------------------------------------------------------
# 13. Protection of active collection (sre_copilot_v1 cannot be wiped)
# -----------------------------------------------------------------------------
def test_protection_of_active_collection_safeguards():
    syncer = CorpusSynchronizer(collection="sre_copilot_v1")

    # Wildcard deletion forbidden
    with pytest.raises(ValueError, match="Wildcards and root paths are forbidden"):
        syncer.delete_document("*", confirm=True)

    # Root path deletion forbidden
    with pytest.raises(ValueError, match="Wildcards and root paths are forbidden"):
        syncer.delete_document("/", confirm=True)

    # Path traversal forbidden
    with pytest.raises(ValueError, match="Path traversal detected"):
        syncer.delete_document("../../etc/shadow", confirm=True)

    # Unconfirmed deletion forbidden
    with pytest.raises(ValueError, match="requires explicit confirmation"):
        syncer.delete_document("kubernetes/workloads/daemonsets.md", confirm=False)


# -----------------------------------------------------------------------------
# 14. Protection of baseline collections (k8s_ops, k8s_ops_bge_small)
# -----------------------------------------------------------------------------
def test_protection_of_baseline_collections():
    for base_col in ("k8s_ops", "k8s_ops_bge_small"):
        syncer = CorpusSynchronizer(collection=base_col)

        # Deletion forbidden
        with pytest.raises(ValueError, match="forbidden on protected baseline collection"):
            syncer.delete_document("any_doc.md", confirm=True)

        # Sync execution forbidden
        from advanced_rag.ingestion.sync import SyncPlan

        plan = SyncPlan(collection=base_col, corpus_path=Path("data/corpus"))
        with pytest.raises(ValueError, match="forbidden on protected baseline collection"):
            syncer.execute_sync(plan, confirm=True)

        # CLI recreation forbidden
        ret = cli.main(["--collection", base_col, "--recreate", "--dry-run"])
        assert ret == 1


# -----------------------------------------------------------------------------
# 15. Paths containing spaces
# -----------------------------------------------------------------------------
def test_paths_containing_spaces(tmp_path, temp_store):
    space_folder = tmp_path / "incident runbooks 2026"
    space_folder.mkdir(parents=True)
    space_file = space_folder / "oom killer remediation.md"
    space_file.write_text(
        "# OOM Remediation\n\nIncrease container memory request when exit code 137 is observed in pod events.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan = syncer.create_plan(space_folder, store=temp_store)
    assert len(plan.new_documents) == 1

    res = syncer.execute_sync(plan, store=temp_store)
    assert res["upserted_chunks"] == 1
    assert temp_store.count() == 1

    # Safe deletion of path with spaces
    del_res = syncer.delete_document(
        plan.new_documents[0].source,
        store=temp_store,
        confirm=True,
    )
    assert del_res["deleted_chunks"] == 1
    assert temp_store.count() == 0


# -----------------------------------------------------------------------------
# 16. Nested corpus directory discovery
# -----------------------------------------------------------------------------
def test_nested_corpus_directory_discovery(tmp_path, temp_store):
    nested_dir = tmp_path / "deep" / "nested" / "cluster" / "admin"
    nested_dir.mkdir(parents=True)
    f1 = nested_dir / "etcd_backup.md"
    f1.write_text(
        "# etcd Backup Procedure\n\nRun etcdctl snapshot save /var/backup/etcd.db to preserve cluster state.",
        encoding="utf-8",
    )

    f2 = tmp_path / "deep" / "network.md"
    f2.write_text(
        "# CNI Diagnosis Guide\n\nInspect Cilium and Calico agent logs when network policies drop traffic.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan = syncer.create_plan(tmp_path, store=temp_store)
    assert len(plan.new_documents) == 2

    syncer.execute_sync(plan, store=temp_store)
    assert temp_store.count() == 2

    indexed = temp_store.get_indexed_documents()
    sources = set(indexed.keys())
    assert any("etcd_backup.md" in s for s in sources)
    assert any("network.md" in s for s in sources)


# -----------------------------------------------------------------------------
# 17. Metadata and citations preserved after synchronization
# -----------------------------------------------------------------------------
def test_metadata_and_citations_preserved_after_sync(tmp_path, temp_store):
    doc_path = tmp_path / "custom_doc.md"
    doc_path.write_text(
        "---\n"
        "title: Custom Ingress Runbook\n"
        "doc_type: runbook\n"
        "category: networking\n"
        "component: ingress-nginx\n"
        "---\n\n"
        "# Diagnostics Procedures\n\n"
        "Check controller ingress logs with kubectl logs -n ingress-nginx to diagnose TLS termination issues.",
        encoding="utf-8",
    )

    syncer = CorpusSynchronizer(collection=temp_store.collection)
    plan = syncer.create_plan(tmp_path, store=temp_store)
    syncer.execute_sync(plan, store=temp_store)

    # Search and verify chunk metadata and citation
    results = temp_store.search("ingress-nginx controller logs")
    assert len(results) > 0
    top = results[0]
    assert top.chunk.title == "Custom Ingress Runbook"
    assert top.chunk.doc_type == "runbook"
    assert top.chunk.metadata.get("category") == "networking"
    assert top.chunk.metadata.get("component") == "ingress-nginx"
    assert "Custom Ingress Runbook" in top.chunk.citation()
