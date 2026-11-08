"""Comprehensive integration and regression tests for Stage 3A Ingestion Engine improvements.

Covers all 18 requirements:
1. Markdown: Title extraction, frontmatter parsing, section splitting, code block preservation.
2. PDF: Page-by-page extraction, page number metadata, title extraction.
3. PDF: Actionable error message for image-only/scanned PDFs without OCR.
4. DOCX: Paragraphs, headings, table extraction with preserved structure.
5. TXT: Multi-encoding fallback, line ending normalization, stack trace / log preservation.
6. CSV: Header preservation, row-as-section, multi-line fields.
7. HTML: Title extraction, heading hierarchy, boilerplate removal, table preservation.
8. JSON: Key-path formatting, array splitting, valid JSON validation.
9. Empty file handling for all formats (graceful descriptive ValueError).
10. Malformed file handling (corrupt PDF, broken DOCX, invalid JSON).
11. Large document handling and oversized table header repetition.
12. Metadata preservation: source, title, doc_type, section, page_number, row_number, etc.
13. Deterministic chunk IDs: same input -> same chunk IDs regardless of format.
14. Deduplication of identical chunks.
15. CLI dry-run mode reporting format breakdown, statistics, and sample chunks.
16. CLI single-file ingestion mode.
17. CLI protected collection guards (k8s_ops, k8s_ops_bge_small).
18. Backward compatibility with existing 252 corpus documents and 82-chunk legacy baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
import uuid

import docx
import pytest

from advanced_rag.config import get_settings
from advanced_rag.ingestion import cli
from advanced_rag.retrieval.vectorstore import HybridStore, _point_id
from advanced_rag.ingestion.chunker import chunk_document, Section
from advanced_rag.ingestion.extractors.cleaner import is_stack_trace_or_log, normalize_text
from advanced_rag.ingestion.loader import (
    CORPUS_DIR,
    chunk_corpus,
    load_corpus,
    load_document,
    load_path,
)

FIXTURES_DIR = Path("tests/fixtures")


def test_markdown_extraction_and_frontmatter():
    """Verify markdown extractor captures all frontmatter fields, sections, and preserves code blocks."""
    md_file = FIXTURES_DIR / "sample_runbook.md"
    doc = load_document(md_file)

    assert doc.title == "Production Node Evacuation Runbook"
    assert doc.doc_type == "runbook"
    assert doc.file_type in ("md", "markdown")
    assert doc.metadata["category"] == "operation"
    assert doc.metadata["component"] == "kubelet"
    assert doc.metadata["source_url"] == "https://internal.sre.corp/runbooks/node-evacuation"
    assert doc.metadata["source_version"] == "2.1.0"

    # Verify code blocks and indentation preserved
    assert "kubectl cordon node-01.corp.internal" in doc.body
    assert "kubectl drain node-01.corp.internal --ignore-daemonsets" in doc.body

    chunks = chunk_corpus([doc])
    assert len(chunks) >= 2
    assert any("Eviction Command" in c.section for c in chunks)
    assert any("Production Node Evacuation Runbook - Eviction Command" in c.citation() for c in chunks)
    assert all(c.file_type in ("md", "markdown") for c in chunks)
    assert all(c.metadata.get("component") == "kubelet" for c in chunks)


def test_pdf_page_level_extraction():
    """Verify PDF extractor captures page numbers, page counts, and detected page headings."""
    pdf_file = FIXTURES_DIR / "sample_doc.pdf"
    doc = load_document(pdf_file)

    assert doc.doc_type == "pdf"
    assert doc.file_type == "pdf"
    assert doc.sections is not None
    assert len(doc.sections) == 2

    # Check page 1
    p1 = doc.sections[0]
    assert p1.metadata["page_number"] == 1
    assert p1.metadata["page_count"] == 2
    assert "Cluster Architecture Guidelines" in p1.title
    assert "Kubernetes worker nodes require container runtime" in p1.body

    # Check page 2
    p2 = doc.sections[1]
    assert p2.metadata["page_number"] == 2
    assert "Storage Class Configurations" in p2.title
    assert "PersistentVolumes must specify CSI provisioner rules" in p2.body

    chunks = chunk_corpus([doc])
    assert len(chunks) == 2
    assert chunks[0].metadata["page_number"] == 1
    assert chunks[1].metadata["page_number"] == 2
    assert "Page 1 - Cluster Architecture Guidelines" in chunks[0].citation()


def test_pdf_image_only_raises_actionable_error():
    """Verify that a PDF without extractable text raises an explicit OCR warning."""
    image_pdf = FIXTURES_DIR / "image_only.pdf"
    with pytest.raises(ValueError) as excinfo:
        load_document(image_pdf)
    assert "no extractable text" in str(excinfo.value)
    assert "Scanned or image-only PDFs require OCR processing" in str(excinfo.value)


def test_docx_hierarchy_and_table_preservation():
    """Verify DOCX extractor captures headings, core properties, and formats tables as Markdown."""
    docx_file = FIXTURES_DIR / "sample_doc.docx"
    doc = load_document(docx_file)

    assert doc.title == "Kubernetes Storage Troubleshooting"
    assert doc.metadata.get("author") == "SRE Team"
    assert doc.metadata.get("category") == "Storage"
    assert doc.doc_type == "docx"

    # Verify heading and paragraph
    assert any("Volume Attachment Verification" in s.title for s in doc.sections)
    assert any("Ensure VolumeAttachment object exists" in s.body for s in doc.sections)

    # Verify table structure is rendered as markdown table with headers
    full_text = "\n".join(s.body for s in doc.sections)
    assert "| PV Name | Driver | Status |" in full_text
    assert "| pvc-1001 | ebs.csi.aws.com | Bound |" in full_text
    assert "| pvc-1002 | pd.csi.storage.gke.io | Terminating |" in full_text


def test_txt_encoding_and_stacktrace_preservation():
    """Verify TXT extractor handles UTF-8 BOM and preserves stack traces and timestamps."""
    txt_file = FIXTURES_DIR / "sample_logs.txt"
    doc = load_document(txt_file)

    assert doc.doc_type == "text"
    assert "2026-11-08T10:00:00Z [INFO] Service started" in doc.body
    assert "ConnectionTimeoutError: Connection timed out after 30000ms" in doc.body

    # Verify helper recognizes log and traceback patterns
    assert is_stack_trace_or_log("2026-11-08T10:00:00Z [INFO] Service started")
    assert is_stack_trace_or_log("Traceback (most recent call last):")
    assert is_stack_trace_or_log("  File \"/app/workers/ingest.py\", line 142, in process_batch")

    chunks = chunk_corpus([doc])
    assert chunks
    # Traceback should not be stripped of indentation
    assert any("  File \"/app/workers/ingest.py\"" in c.text for c in chunks)


def test_csv_headers_and_row_sections():
    """Verify CSV extractor parses rows as sections with column headers and row_number metadata."""
    csv_file = FIXTURES_DIR / "sample_data.csv"
    doc = load_document(csv_file)

    assert doc.doc_type == "csv"
    assert doc.sections is not None
    assert len(doc.sections) == 3

    assert doc.sections[0].title == "Row 1"
    assert doc.sections[0].metadata["row_number"] == 1
    assert "alert_id: ALR-001" in doc.sections[0].body
    assert "service: ingress-nginx" in doc.sections[0].body

    chunks = chunk_corpus([doc])
    assert len(chunks) == 3
    assert chunks[0].section == "Row 1"
    assert chunks[0].metadata["row_number"] == 1
    assert chunks[0].citation() == "Sample Data - Row 1"


def test_html_boilerplate_removal_and_hierarchy():
    """Verify HTML extractor removes scripts/styles/nav/footers and extracts headings and tables."""
    html_file = FIXTURES_DIR / "sample_page.html"
    doc = load_document(html_file)

    assert doc.title == "Kubernetes Cluster Autoscaler Guide"
    assert doc.doc_type == "html"
    assert doc.metadata.get("source_url") == "https://k8s-docs.corp/autoscaler-guide"

    # Verify script, style, nav, and footer are stripped
    assert "analytics tracking code" not in doc.body
    assert "display: none" not in doc.body
    assert "Skip to main" not in doc.body
    assert "Internal documentation - Copyright" not in doc.body

    # Verify headings, paragraphs, and tables are preserved
    assert "Cluster Autoscaling Strategies" in doc.body
    assert "Scale Down Policies" in doc.body
    assert "| Setting | Default | Description |" in doc.body
    assert "kubectl logs -n kube-system deployment/cluster-autoscaler" in doc.body


def test_json_nested_structure_and_arrays():
    """Verify JSON extractor formats nested objects and splits arrays into labeled sections."""
    json_file = FIXTURES_DIR / "sample_config.json"
    doc = load_document(json_file)

    assert doc.doc_type == "json"
    assert doc.sections is not None
    assert len(doc.sections) >= 3

    assert any(s.title == "cluster" for s in doc.sections)
    assert any("networking" in s.title for s in doc.sections)
    assert "prod-useast1" in doc.body
    assert "cni: cilium" in doc.body

    alert_sections = [s for s in doc.sections if "Alert" in s.title or "NodeMemoryPressure" in s.title or "alerts" in s.title]
    assert alert_sections
    assert any("Trigger scale up" in s.body for s in alert_sections)


def test_empty_files_handling_all_formats(tmp_path: Path):
    """Verify empty files across all 7 formats raise descriptive ValueErrors."""
    extensions = [".md", ".pdf", ".docx", ".txt", ".csv", ".html", ".json"]
    for ext in extensions:
        empty_file = tmp_path / f"empty_test{ext}"
        empty_file.write_bytes(b"")
        with pytest.raises(ValueError, match="empty|corrupted|invalid|cannot read") as excinfo:
            load_document(empty_file)
        assert len(str(excinfo.value)) > 0


def test_malformed_files_handling(tmp_path: Path):
    """Verify malformed and corrupted files across formats raise actionable ValueErrors."""
    # Corrupt PDF
    bad_pdf = tmp_path / "corrupt.pdf"
    bad_pdf.write_bytes(b"%PDF-1.4 header without any valid objects or trailer")
    with pytest.raises(ValueError, match="Corrupted or invalid PDF"):
        load_document(bad_pdf)

    # Corrupt DOCX (not a valid zip container)
    bad_docx = tmp_path / "corrupt.docx"
    bad_docx.write_bytes(b"This is plain text not a zip archive PK structure")
    with pytest.raises(ValueError, match="Corrupted or invalid DOCX"):
        load_document(bad_docx)

    # Malformed JSON
    bad_json = tmp_path / "corrupt.json"
    bad_json.write_text('{"key": "value", unquoted}', encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed JSON"):
        load_document(bad_json)


def test_large_document_windowing_and_table_preservation():
    """Verify that an oversized table split across windows replicates the header on every chunk."""
    # Construct a large markdown table with 30 rows
    table_header = "| Step | Action | Expected Output | Status |\n|---|---|---|---|\n"
    table_rows = "\n".join(
        f"| {i} | Verify node-{i:02d} health metrics | Status should be Ready | Passed |"
        for i in range(1, 35)
    )
    oversized_table = f"# Maintenance Checklist\n\n{table_header}{table_rows}\n"

    chunks = chunk_document(
        text=oversized_table,
        source="checklist.md",
        title="Maintenance Checklist",
        doc_type="runbook",
        chunk_size=300,
        chunk_overlap=50,
    )
    assert len(chunks) > 1
    # Every chunk that contains table rows should also retain the table header
    for c in chunks:
        if "Verify node-" in c.text:
            assert "| Step | Action | Expected Output | Status |" in c.text
            assert "|---|---|---|---|" in c.text


def test_metadata_preservation_across_all_formats():
    """Verify that all chunks across all formats retain required metadata attributes."""
    doc_paths = [
        FIXTURES_DIR / "sample_runbook.md",
        FIXTURES_DIR / "sample_doc.pdf",
        FIXTURES_DIR / "sample_doc.docx",
        FIXTURES_DIR / "sample_logs.txt",
        FIXTURES_DIR / "sample_data.csv",
        FIXTURES_DIR / "sample_page.html",
        FIXTURES_DIR / "sample_config.json",
    ]
    for p in doc_paths:
        doc = load_document(p)
        chunks = chunk_corpus([doc])
        assert chunks, f"No chunks generated for {p.name}"
        for chunk in chunks:
            assert chunk.source
            assert chunk.title
            assert chunk.document_id
            assert chunk.content_hash
            assert chunk.extraction_format
            assert chunk.file_type
            assert chunk.id
            payload = chunk.payload()
            assert "source" in payload
            assert "title" in payload
            assert "document_id" in payload
            assert "content_hash" in payload
            assert "extraction_format" in payload


def test_deterministic_chunk_and_point_ids():
    """Verify that identical documents produce identical deterministic chunk IDs and valid UUID point IDs."""
    doc_paths = [
        FIXTURES_DIR / "sample_runbook.md",
        FIXTURES_DIR / "sample_doc.pdf",
        FIXTURES_DIR / "sample_doc.docx",
        FIXTURES_DIR / "sample_logs.txt",
        FIXTURES_DIR / "sample_data.csv",
        FIXTURES_DIR / "sample_page.html",
        FIXTURES_DIR / "sample_config.json",
    ]
    for p in doc_paths:
        doc1 = load_document(p)
        doc2 = load_document(p)
        chunks1 = chunk_corpus([doc1])
        chunks2 = chunk_corpus([doc2])
        ids1 = [c.id for c in chunks1]
        ids2 = [c.id for c in chunks2]
        assert ids1 == ids2, f"Chunk IDs were not deterministic for {p.name}"

        # Verify point ID is a valid UUID derived deterministically
        for cid in ids1:
            pid = _point_id(cid)
            parsed_uuid = uuid.UUID(pid)
            assert str(parsed_uuid) == pid


def test_deduplication_of_chunks():
    """Verify that duplicated documents produce identical IDs and can be cleanly deduplicated."""
    doc = load_document(FIXTURES_DIR / "sample_data.csv")
    chunks = chunk_corpus([doc, doc])  # Duplicate the document
    assert len(chunks) == 6  # 3 rows x 2

    # Deduplicate by ID
    unique_ids = {c.id for c in chunks}
    assert len(unique_ids) == 3


def test_cli_dry_run_output(capsys):
    """Verify CLI dry run prints format breakdown, character statistics, and sample chunk citations."""
    ret = cli.main(["--path", str(FIXTURES_DIR), "--dry-run"])
    assert ret == 0

    captured = capsys.readouterr()
    stdout = captured.out
    assert "Loaded 7 document(s)" in stdout  # image_only.pdf skipped in lenient mode
    assert "Document formats:" in stdout
    assert "csv: 1" in stdout
    assert "docx: 1" in stdout
    assert "html: 1" in stdout
    assert "json: 1" in stdout
    assert "md: 1" in stdout
    assert "pdf: 1" in stdout
    assert "txt: 1" in stdout
    assert "Chunk characters: min=" in stdout
    assert "median=" in stdout
    assert "max=" in stdout
    assert "--- Dry Run Report (no writes to Qdrant) ---" in stdout
    assert "[Sample Chunk 1/" in stdout
    assert "Citation:" in stdout
    assert "Dry run complete for 7 document(s)" in stdout


def test_cli_single_file_ingestion(capsys):
    """Verify CLI can target a single file using positional or --file arguments."""
    # Positional target
    ret1 = cli.main([str(FIXTURES_DIR / "sample_runbook.md"), "--dry-run"])
    assert ret1 == 0
    out1 = capsys.readouterr().out
    assert "Loaded 1 document(s)" in out1
    assert "md: 1" in out1

    # --file flag
    ret2 = cli.main(["--file", str(FIXTURES_DIR / "sample_data.csv"), "--dry-run"])
    assert ret2 == 0
    out2 = capsys.readouterr().out
    assert "Loaded 1 document(s)" in out2
    assert "csv: 1" in out2


def test_cli_strict_mode_fails_on_image_only(capsys):
    """Verify CLI in strict mode fails immediately when encountering image_only.pdf."""
    ret = cli.main(["--path", str(FIXTURES_DIR), "--strict", "--dry-run"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "Error loading documents" in err
    assert "Scanned or image-only PDFs require OCR processing" in err


def test_cli_baseline_protection(capsys):
    """Verify CLI protects baseline collections k8s_ops and k8s_ops_bge_small from recreation."""
    ret1 = cli.main(["--path", str(FIXTURES_DIR), "--collection", "k8s_ops", "--recreate"])
    assert ret1 == 1
    err1 = capsys.readouterr().err
    assert "Protected baseline collection 'k8s_ops' cannot be dropped or recreated" in err1

    ret2 = cli.main(["--path", str(FIXTURES_DIR), "--collection", "k8s_ops_bge_small", "--recreate"])
    assert ret2 == 1
    err2 = capsys.readouterr().err
    assert "Protected baseline collection 'k8s_ops_bge_small' cannot be dropped or recreated" in err2


def test_backward_compatibility_with_existing_corpus():
    """Verify that existing legacy backup produces exactly 82 chunks and active corpus has 252 documents."""
    legacy_backup = Path("data/legacy_corpus_backup")
    if legacy_backup.exists():
        legacy_docs = load_corpus(legacy_backup)
        assert len(legacy_docs) == 9
        legacy_chunks = chunk_corpus(legacy_docs)
        assert len(legacy_chunks) == 82

    # Active corpus check (verify 252 documents remain intact and unchanged)
    active_docs = load_corpus(CORPUS_DIR)
    assert len(active_docs) == 252
    k8s_count = sum(1 for d in active_docs if d.source.startswith("kubernetes/"))
    helm_count = sum(1 for d in active_docs if d.source.startswith("helm/"))
    prom_count = sum(1 for d in active_docs if d.source.startswith("prometheus/"))
    sre_count = sum(1 for d in active_docs if d.source.startswith("sre/"))
    assert k8s_count == 64
    assert helm_count == 64
    assert prom_count == 64
    assert sre_count == 60


def test_docx_alternating_paragraphs_and_multiple_tables(tmp_path: Path):
    """Verify DOCX extractor maintains alternating paragraphs and multiple tables in exact document order."""
    docx_file = tmp_path / "multi_table.docx"
    doc = docx.Document()
    doc.add_heading("Section Alpha", level=1)
    doc.add_paragraph("Paragraph before first table.")
    t1 = doc.add_table(rows=2, cols=2)
    t1.rows[0].cells[0].text = "Header A1"
    t1.rows[0].cells[1].text = "Header A2"
    t1.rows[1].cells[0].text = "Val A1"
    t1.rows[1].cells[1].text = "Val A2"

    doc.add_heading("Section Beta", level=1)
    doc.add_paragraph("Paragraph before second table.")
    t2 = doc.add_table(rows=2, cols=2)
    t2.rows[0].cells[0].text = "Header B1"
    t2.rows[0].cells[1].text = "Header B2"
    t2.rows[1].cells[0].text = "Val B1"
    t2.rows[1].cells[1].text = "Val B2"
    doc.save(str(docx_file))

    extracted = load_document(docx_file)
    assert extracted.sections is not None
    assert len(extracted.sections) == 2

    # Section Alpha should contain Table 1 and NOT Table 2
    sec_alpha = extracted.sections[0]
    assert "Section Alpha" in sec_alpha.title
    assert "Paragraph before first table" in sec_alpha.body
    assert "| Header A1 | Header A2 |" in sec_alpha.body
    assert "Header B1" not in sec_alpha.body

    # Section Beta should contain Table 2 and NOT Table 1
    sec_beta = extracted.sections[1]
    assert "Section Beta" in sec_beta.title
    assert "Paragraph before second table" in sec_beta.body
    assert "| Header B1 | Header B2 |" in sec_beta.body
    assert "Header A1" not in sec_beta.body


def test_deterministic_id_and_collision_safety(tmp_path: Path):
    """Verify chunk and point ID uniqueness, collision safety across files/sections, and idempotency."""
    identical_body = "Execute kubectl rollout restart deployment/coredns -n kube-system to refresh cache."

    # 1. Same content in two different files -> distinct IDs
    f1 = tmp_path / "runbook_a.md"
    f1.write_text(f"# Remediation\n\n{identical_body}\n", encoding="utf-8")
    f2 = tmp_path / "runbook_b.md"
    f2.write_text(f"# Remediation\n\n{identical_body}\n", encoding="utf-8")

    doc1 = load_document(f1)
    doc2 = load_document(f2)
    c1 = chunk_corpus([doc1])
    c2 = chunk_corpus([doc2])

    assert len(c1) == 1 and len(c2) == 1
    assert c1[0].id.startswith("runbook_a.md#")
    assert c2[0].id.startswith("runbook_b.md#")
    assert c1[0].id != c2[0].id
    assert _point_id(c1[0].id) != _point_id(c2[0].id)

    # 2. Different sections with identical text in same file -> distinct IDs
    f3 = tmp_path / "duplicate_sections.md"
    f3.write_text(f"# Step 1\n\n{identical_body}\n\n# Step 2\n\n{identical_body}\n", encoding="utf-8")
    doc3 = load_document(f3)
    c3 = chunk_corpus([doc3])
    assert len(c3) == 2
    assert c3[0].section == "Step 1"
    assert c3[1].section == "Step 2"
    assert c3[0].id != c3[1].id
    assert _point_id(c3[0].id) != _point_id(c3[1].id)

    # 3. Changed document content -> new chunk ID
    f1_modified = tmp_path / "runbook_a_v2.md"
    f1_modified.write_text(f"# Remediation\n\n{identical_body} Additional steps.\n", encoding="utf-8")
    doc1_mod = load_document(f1_modified)
    c1_mod = chunk_corpus([doc1_mod])
    assert c1_mod[0].id != c1[0].id

    # 4. Upsert into isolated temporary Qdrant collection to verify coexistence & idempotency
    temp_col = f"test_col_{uuid.uuid4().hex[:8]}"
    settings = get_settings()
    store = HybridStore(settings=settings, collection=temp_col)
    try:
        store.ensure_collection(recreate=True)
        # Upsert both documents with identical paragraph
        written = store.upsert([c1[0], c2[0]])
        assert written == 2
        assert store.count() == 2

        # Re-upserting f1 must be idempotent (no growth)
        re_written = store.upsert([c1[0]])
        assert re_written == 1
        assert store.count() == 2

        # Verify citation metadata preserved separately for both points
        p1 = store.client.retrieve(temp_col, ids=[_point_id(c1[0].id)])
        p2 = store.client.retrieve(temp_col, ids=[_point_id(c2[0].id)])
        assert len(p1) == 1 and len(p2) == 1
        assert p1[0].payload["source"] == "runbook_a.md"
        assert p2[0].payload["source"] == "runbook_b.md"
    finally:
        try:
            store.client.delete_collection(temp_col)
        except Exception:
            pass
        store.close()


def test_cli_paths_with_spaces_and_error_handling(tmp_path: Path, capsys):
    """Verify CLI handles Windows directory/file paths with spaces, and returns appropriate exit codes."""
    space_dir = tmp_path / "Operations Directory with Spaces"
    space_dir.mkdir(parents=True, exist_ok=True)
    space_file = space_dir / "Production Runbook Guide.md"
    space_file.write_text("# Guide\n\nRunbook with spaces in filename.\n", encoding="utf-8")

    # Directory with spaces
    ret_dir = cli.main(["--path", str(space_dir), "--dry-run"])
    assert ret_dir == 0
    out_dir = capsys.readouterr().out
    assert "Loaded 1 document(s)" in out_dir

    # Single file with spaces
    ret_file = cli.main(["--file", str(space_file), "--dry-run"])
    assert ret_file == 0
    out_file = capsys.readouterr().out
    assert "Loaded 1 document(s)" in out_file

    # Nonexistent path returns exit code 1 with error on stderr
    missing_path = tmp_path / "nonexistent_file.pdf"
    ret_missing = cli.main(["--path", str(missing_path)])
    assert ret_missing == 1
    err_missing = capsys.readouterr().err
    assert "Error: Target path does not exist" in err_missing

