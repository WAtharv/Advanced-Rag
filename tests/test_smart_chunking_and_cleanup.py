"""Tests for smart chunking, standardized metadata consistency, and safe document replacement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from qdrant_client import QdrantClient

from advanced_rag.ingestion.chunker import Section, chunk_document
from advanced_rag.ingestion.loader import chunk_corpus, load_corpus, load_document
from advanced_rag.models import Chunk
from advanced_rag.retrieval.embeddings import KeywordEmbedder
from advanced_rag.retrieval.vectorstore import HybridStore


@pytest.fixture
def memory_store():
    """Isolated, in-memory Qdrant store that never touches production storage."""
    client = QdrantClient(":memory:")
    store = HybridStore(
        collection="test_smart_chunking",
        embedder=KeywordEmbedder(),
        client=client,
    )
    store.ensure_collection()
    return store


def test_metadata_consistency_standardized(tmp_path: Path):
    """Test that every Chunk carries standardized metadata attributes and payload keys."""
    # 1. Plain text / Markdown
    md_file = tmp_path / "sample.md"
    md_file.write_text(
        "---\ntitle: Sample Guide\ndoc_type: runbook\n---\n\n"
        "# Section Alpha\n\nDetailed operational instructions for kubernetes cluster.\n",
        encoding="utf-8",
    )
    doc = load_document(md_file)
    chunks = chunk_corpus([doc])
    assert len(chunks) >= 1
    c = chunks[0]

    # Verify standard attributes
    assert c.source == "sample.md"
    assert c.title == "Sample Guide"
    assert c.file_type == "md"
    assert c.section == "Section Alpha"
    assert c.document_id
    assert c.content_hash
    assert c.citation() == "Sample Guide - Section Alpha"

    # Verify standard payload keys
    payload = c.payload()
    assert payload["chunk_id"] == c.id
    assert payload["source"] == "sample.md"
    assert payload["file_type"] == "md"
    assert payload["document_id"] == c.document_id
    assert payload["content_hash"] == c.content_hash


def test_pdf_page_references_in_metadata(tmp_path: Path):
    """Test that PDF page numbers are strictly preserved in chunk metadata and payload."""
    sections = [
        Section(
            title="Page 4",
            body="Network policy egress rules and security guidelines for production pods.",
            metadata={"page": 4, "page_number": 4, "file_type": "pdf"},
        )
    ]
    chunks = chunk_document(
        source="network-guide.pdf",
        title="Network Guide",
        sections=sections,
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.page_number == 4
    assert chunk.section == "Page 4"
    assert chunk.citation() == "Network Guide - Page 4"

    payload = chunk.payload()
    assert payload["page_number"] == 4
    assert payload["file_type"] == "pdf"


def test_csv_row_references_in_metadata(tmp_path: Path):
    """Test that CSV row numbers are strictly preserved in chunk metadata and payload."""
    sections = [
        Section(
            title="Row 12",
            body="incident_id: INC-1012\nseverity: SEV1\nservice: checkout-service",
            metadata={"row": 12, "row_number": 12, "file_type": "csv"},
        )
    ]
    chunks = chunk_document(
        source="outages.csv",
        title="Outages",
        sections=sections,
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.row_number == 12
    assert chunk.section == "Row 12"
    assert chunk.citation() == "Outages - Row 12"

    payload = chunk.payload()
    assert payload["row_number"] == 12
    assert payload["file_type"] == "csv"


def test_identical_document_reingestion_no_growth(memory_store: HybridStore):
    """Re-ingesting identical content must not increase Qdrant collection size."""
    chunks = [
        Chunk(
            id="doc1#001",
            text="First chunk of the operational runbook troubleshooting guide.",
            source="runbook.md",
            title="Runbook",
            section="Section 1",
        ),
        Chunk(
            id="doc1#002",
            text="Second chunk of the operational runbook troubleshooting guide.",
            source="runbook.md",
            title="Runbook",
            section="Section 2",
        ),
    ]

    res1 = memory_store.replace_document(chunks, source="runbook.md")
    assert res1["upserted"] == 2
    assert res1["deleted_obsolete"] == 0
    assert memory_store.count() == 2

    # Re-ingest exact same chunks
    res2 = memory_store.replace_document(chunks, source="runbook.md")
    assert res2["upserted"] == 2
    assert res2["deleted_obsolete"] == 0
    assert memory_store.count() == 2  # No duplicate growth!


def test_shortened_and_modified_document_reingestion(memory_store: HybridStore):
    """When a document is edited or shortened, obsolete chunk points must be deleted."""
    # Version 1: 3 chunks
    chunks_v1 = [
        Chunk(
            id="doc1#alpha",
            text="Alpha chunk content describing pod restart investigation procedures.",
            source="runbook.md",
            title="Runbook",
            section="Alpha",
        ),
        Chunk(
            id="doc1#beta",
            text="Beta chunk content describing container OOMKilled resolution steps.",
            source="runbook.md",
            title="Runbook",
            section="Beta",
        ),
        Chunk(
            id="doc1#gamma",
            text="Gamma chunk content describing ingress routing error diagnosis.",
            source="runbook.md",
            title="Runbook",
            section="Gamma",
        ),
    ]
    memory_store.replace_document(chunks_v1, source="runbook.md")
    assert memory_store.count() == 3

    # Version 2: Shortened (only Alpha modified and Gamma removed; Beta kept)
    chunks_v2 = [
        Chunk(
            id="doc1#alpha_modified",
            text="Alpha chunk MODIFIED with new kubectl diagnostic commands.",
            source="runbook.md",
            title="Runbook",
            section="Alpha",
        ),
        Chunk(
            id="doc1#beta",
            text="Beta chunk content describing container OOMKilled resolution steps.",
            source="runbook.md",
            title="Runbook",
            section="Beta",
        ),
    ]

    res = memory_store.replace_document(chunks_v2, source="runbook.md")
    assert res["upserted"] == 2
    # doc1#alpha and doc1#gamma should have been deleted (2 obsolete points)
    assert res["deleted_obsolete"] == 2
    assert memory_store.count() == 2

    # Verify remaining point IDs in Qdrant match v2
    points = memory_store.get_point_ids_by_source("runbook.md")
    assert len(points) == 2


def test_source_specific_cleanup_preserves_unrelated_documents(memory_store: HybridStore):
    """Replacing or deleting one document must never affect chunks from another document."""
    doc_a_chunks = [
        Chunk(
            id="docA#1",
            text="Document A chunk content covering cluster policy rules.",
            source="docA.md",
            title="Doc A",
        )
    ]
    doc_b_chunks = [
        Chunk(
            id="docB#1",
            text="Document B chunk content covering ingress TLS configuration.",
            source="docB.md",
            title="Doc B",
        )
    ]

    memory_store.replace_document(doc_a_chunks, source="docA.md")
    memory_store.replace_document(doc_b_chunks, source="docB.md")
    assert memory_store.count() == 2

    # Replace docA with modified chunk
    doc_a_updated = [
        Chunk(
            id="docA#2",
            text="Document A updated chunk with newer cluster policy specifications.",
            source="docA.md",
            title="Doc A",
        )
    ]
    memory_store.replace_document(doc_a_updated, source="docA.md")
    assert memory_store.count() == 2

    # Verify docB is still present and untouched
    doc_b_points = memory_store.get_point_ids_by_source("docB.md")
    assert len(doc_b_points) == 1

    # Delete docA completely
    del_res = memory_store.delete_document("docA.md")
    assert del_res["deleted_chunks"] == 1
    assert memory_store.count() == 1

    # Verify docB remains intact
    doc_b_points_after = memory_store.get_point_ids_by_source("docB.md")
    assert len(doc_b_points_after) == 1


def test_delete_nonexistent_document(memory_store: HybridStore):
    """Deleting a non-existent document source should return 0 deleted safely."""
    res = memory_store.delete_document("nonexistent.pdf")
    assert res["deleted_chunks"] == 0
    assert not res["found"]


def test_failed_ingestion_safety_preserves_existing_chunks(memory_store: HybridStore):
    """If upsert fails midway, existing points must NOT be deleted."""
    existing_chunks = [
        Chunk(
            id="safe#1",
            text="Valid existing chunk that should not be deleted on update failure.",
            source="safe.md",
            title="Safe",
        )
    ]
    memory_store.replace_document(existing_chunks, source="safe.md")
    assert memory_store.count() == 1

    new_chunks = [
        Chunk(
            id="safe#2",
            text="New chunk that triggers an error during upsert.",
            source="safe.md",
            title="Safe",
        )
    ]

    with patch.object(memory_store, "upsert", side_effect=RuntimeError("Simulated write failure")):
        with pytest.raises(RuntimeError, match="Simulated write failure"):
            memory_store.replace_document(new_chunks, source="safe.md")

    # Original chunk must still exist!
    assert memory_store.count() == 1
    assert len(memory_store.get_point_ids_by_source("safe.md")) == 1


def test_markdown_baseline_regression_preserved():
    """Verify that the 9 legacy markdown files in data/legacy_corpus_backup produce exactly 82 chunks."""
    corpus_docs = load_corpus(Path("data/legacy_corpus_backup"))
    assert len(corpus_docs) == 9
    chunks = chunk_corpus(corpus_docs)
    assert len(chunks) == 82


def test_short_meaningful_content_preserved_for_non_markdown():
    """Verify that meaningful short content in CSV, TXT, and PDF (<40 chars) is preserved."""
    # 1. Short CSV row (24 characters)
    csv_sections = [
        Section(
            title="Row 1",
            body="code: 200\nstatus: OK",
            metadata={"row": 1, "file_type": "csv"},
        )
    ]
    csv_chunks = chunk_document(
        source="status.csv",
        title="Status",
        sections=csv_sections,
    )
    assert len(csv_chunks) == 1
    assert "status: OK" in csv_chunks[0].text
    assert len(csv_chunks[0].text) < 40

    # 2. Short TXT technical definition (27 characters)
    txt_sections = [
        Section(
            title="",
            body="OOMKilled: Exit code 137.",
            metadata={"file_type": "txt"},
        )
    ]
    txt_chunks = chunk_document(
        source="glossary.txt",
        title="Glossary",
        sections=txt_sections,
    )
    assert len(txt_chunks) == 1
    assert "Exit code 137" in txt_chunks[0].text
    assert len(txt_chunks[0].text) < 40

    # 3. Short PDF page section (25 characters)
    pdf_sections = [
        Section(
            title="Page 1",
            body="Cluster health: healthy.",
            metadata={"page": 1, "file_type": "pdf"},
        )
    ]
    pdf_chunks = chunk_document(
        source="health.pdf",
        title="Health",
        sections=pdf_sections,
    )
    assert len(pdf_chunks) == 1
    assert "Cluster health" in pdf_chunks[0].text
    assert len(pdf_chunks[0].text) < 40


def test_empty_or_whitespace_sections_discarded():
    """Verify that pure whitespace or empty sections are safely filtered out."""
    sections = [
        Section(title="Empty 1", body=""),
        Section(title="Empty 2", body="   \n\t   \n"),
        Section(title="Valid", body="Actual operational content for kubernetes cluster."),
    ]
    chunks = chunk_document(
        source="notes.txt",
        title="Notes",
        sections=sections,
    )
    assert len(chunks) == 1
    assert chunks[0].section == "Valid"


