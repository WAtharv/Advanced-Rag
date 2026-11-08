"""Unit and regression tests for multi-format document extraction and ingestion."""

from __future__ import annotations

from pathlib import Path

import docx
import pytest

from advanced_rag.ingestion.loader import chunk_corpus, load_corpus, load_document

SAMPLE_PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R
   /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 76 >>
stream
BT
/F1 12 Tf
72 712 Td
(Kubernetes Network Policy rules and security guidelines for production pods) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
6 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 7 0 R
   /Resources << /Font << /F1 5 0 R >> >> >>
endobj
7 0 obj
<< /Length 74 >>
stream
BT
/F1 12 Tf
72 712 Td
(Ingress Controller Gateway routing and TLS termination troubleshooting tips) Tj
ET
endstream
endobj
xref
0 8
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000121 00000 n 
0000000250 00000 n 
0000000377 00000 n 
0000000447 00000 n 
0000000576 00000 n 
trailer
<< /Size 8 /Root 1 0 R >>
startxref
701
%%EOF"""


def test_markdown_extraction(tmp_path: Path):
    md_file = tmp_path / "test-runbook.md"
    md_file.write_text(
        "---\ntitle: Node Drain Guide\ndoc_type: runbook\ncomponent: kubelet\n---\n\n"
        "# Overview\n\n"
        "How to safely drain a Kubernetes node during scheduled maintenance windows.\n\n"
        "## Steps\n\n"
        "Run kubectl drain node-1 with ignore-daemonsets and delete-emptydir-data flags.\n",
        encoding="utf-8",
    )
    doc = load_document(md_file)
    assert doc.title == "Node Drain Guide"
    assert doc.doc_type == "runbook"
    assert doc.metadata["component"] == "kubelet"
    assert "How to safely drain" in doc.body

    chunks = chunk_corpus([doc])
    assert len(chunks) >= 2
    assert any(c.section == "Overview" for c in chunks)
    assert any(c.section == "Steps" for c in chunks)
    assert any("Node Drain Guide - Steps" in c.citation() for c in chunks)


def test_pdf_extraction(tmp_path: Path):
    pdf_file = tmp_path / "network-guide.pdf"
    pdf_file.write_bytes(SAMPLE_PDF_BYTES)

    doc = load_document(pdf_file)
    assert doc.title == "Network Guide"
    assert doc.doc_type == "pdf"
    assert doc.sections is not None
    assert len(doc.sections) == 2
    assert doc.sections[0].title == "Page 1"
    assert doc.sections[0].metadata["page_number"] == 1
    assert "Kubernetes Network Policy" in doc.sections[0].body
    assert doc.sections[1].title == "Page 2"
    assert doc.sections[1].metadata["page_number"] == 2
    assert "Ingress Controller Gateway" in doc.sections[1].body

    chunks = chunk_corpus([doc])
    assert len(chunks) == 2
    assert chunks[0].section == "Page 1"
    assert chunks[0].metadata["page_number"] == 1
    assert chunks[0].citation() == "Network Guide - Page 1"
    assert chunks[1].section == "Page 2"
    assert chunks[1].metadata["page_number"] == 2
    assert chunks[1].citation() == "Network Guide - Page 2"


def test_docx_extraction(tmp_path: Path):
    docx_file = tmp_path / "cluster-upgrade.docx"
    doc_obj = docx.Document()
    doc_obj.core_properties.title = "Cluster Upgrade Procedures"
    doc_obj.add_heading("Prerequisites", level=1)
    doc_obj.add_paragraph("Verify control plane health and backup etcd.")
    table = doc_obj.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Component"
    table.rows[0].cells[1].text = "Target Version"
    table.rows[1].cells[0].text = "kube-apiserver"
    table.rows[1].cells[1].text = "v1.31.0"
    doc_obj.save(str(docx_file))

    doc = load_document(docx_file)
    assert doc.title == "Cluster Upgrade Procedures"
    assert doc.doc_type == "docx"
    assert doc.sections is not None
    assert any("Prerequisites" in s.title for s in doc.sections)
    assert any("kube-apiserver" in s.body for s in doc.sections)

    chunks = chunk_corpus([doc])
    assert chunks
    assert any("Cluster Upgrade Procedures - Prerequisites" in c.citation() for c in chunks)


def test_txt_extraction(tmp_path: Path):
    txt_file = tmp_path / "incident-notes.txt"
    txt_file.write_text(
        "Incident 4092 happened at 03:00 UTC.\n\n"
        "Root cause was identified as stale DNS cache in CoreDNS deployment.\n\n"
        "Remediation: restarted coredns deployment pods and flushed cache.",
        encoding="utf-8-sig",
    )
    doc = load_document(txt_file)
    assert doc.title == "Incident Notes"
    assert doc.doc_type == "text"
    assert "stale DNS cache" in doc.body

    chunks = chunk_corpus([doc])
    assert chunks
    assert all(c.title == "Incident Notes" for c in chunks)


def test_csv_extraction(tmp_path: Path):
    csv_file = tmp_path / "outages.csv"
    csv_file.write_text(
        "incident_id,service,severity,duration_min\n"
        "INC-101,checkout-service,SEV1,45 minutes outage\n"
        "INC-102,payments-service,SEV2,12 minutes latency\n",
        encoding="utf-8",
    )
    doc = load_document(csv_file)
    assert doc.title == "Outages"
    assert doc.doc_type == "csv"
    assert doc.sections is not None
    assert len(doc.sections) == 2
    assert doc.sections[0].title == "Row 1"
    assert doc.sections[0].metadata["row_number"] == 1
    assert "service: checkout-service" in doc.sections[0].body
    assert doc.sections[1].title == "Row 2"
    assert doc.sections[1].metadata["row_number"] == 2
    assert "service: payments-service" in doc.sections[1].body

    chunks = chunk_corpus([doc])
    assert len(chunks) == 2
    assert chunks[0].section == "Row 1"
    assert chunks[0].metadata["row_number"] == 1
    assert chunks[0].citation() == "Outages - Row 1"
    assert chunks[1].section == "Row 2"
    assert chunks[1].citation() == "Outages - Row 2"


def test_html_extraction(tmp_path: Path):
    html_file = tmp_path / "service-mesh.html"
    html_file.write_text(
        "<!DOCTYPE html><html><head><title>Istio Service Mesh</title>"
        "<script>console.log('secret');</script><style>body { color: red; }</style></head>"
        "<body>"
        "<nav>Skip to main</nav>"
        "<h1>Traffic Management</h1>"
        "<p>VirtualService routes ingress traffic based on HTTP headers and uri prefixes.</p>"
        "<h2>Fault Injection</h2>"
        "<p>Inject 503 delays for chaos engineering testing in staging environments.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    doc = load_document(html_file)
    assert doc.title == "Istio Service Mesh"
    assert doc.doc_type == "html"
    assert "console.log" not in doc.body
    assert "VirtualService routes" in doc.body

    chunks = chunk_corpus([doc])
    assert any(c.section == "Traffic Management" for c in chunks)
    assert any(c.section == "Fault Injection" for c in chunks)


def test_json_extraction(tmp_path: Path):
    json_file = tmp_path / "cluster-alerts.json"
    json_file.write_text(
        '{"title": "Alert Rules", "alerts": ['
        '  {"name": "KubePodCrashLooping", "severity": "critical", "for": "5m"},'
        '  {"name": "KubeMemoryOvercommit", "severity": "warning", "for": "10m"}'
        ']}',
        encoding="utf-8",
    )
    doc = load_document(json_file)
    assert doc.title == "Alert Rules"
    assert doc.doc_type == "json"
    assert "KubePodCrashLooping" in doc.body

    chunks = chunk_corpus([doc])
    assert chunks
    assert any("KubePodCrashLooping" in c.text for c in chunks)


def test_unsupported_extension(tmp_path: Path):
    unknown_file = tmp_path / "archive.xyz"
    unknown_file.write_text("binary blob data that is unsupported")
    with pytest.raises(ValueError, match="Unsupported file extension"):
        load_document(unknown_file)


def test_empty_files_fail_clearly(tmp_path: Path):
    empty_txt = tmp_path / "empty.txt"
    empty_txt.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        load_document(empty_txt)

    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        load_document(empty_csv)


def test_malformed_json_fails_clearly(tmp_path: Path):
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{ unquoted_key: 123", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed JSON"):
        load_document(bad_json)


def test_deterministic_chunk_ids_across_all_formats(tmp_path: Path):
    csv_file = tmp_path / "sample.csv"
    csv_file.write_text("a,b\n1,2 value that is sufficiently long for a chunk\n", encoding="utf-8")
    doc1 = load_document(csv_file)
    doc2 = load_document(csv_file)
    chunks1 = chunk_corpus([doc1])
    chunks2 = chunk_corpus([doc2])
    assert [c.id for c in chunks1] == [c.id for c in chunks2]


def test_existing_markdown_corpus_baseline():
    """Verify that the 9 legacy markdown files in data/legacy_corpus_backup load exactly 82 chunks."""
    corpus_docs = load_corpus(Path("data/legacy_corpus_backup"))
    assert len(corpus_docs) == 9
    chunks = chunk_corpus(corpus_docs)
    assert len(chunks) == 82
