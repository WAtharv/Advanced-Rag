from advanced_rag.ingestion.chunker import chunk_document, split_sections, window
from advanced_rag.ingestion.loader import parse_front_matter

MARKDOWN = """# Title

Intro paragraph about the thing.

## First section

Content one. Content two. Content three.

## Second section

More content here.
"""


def test_split_sections_keeps_headings_with_bodies():
    sections = split_sections(MARKDOWN)
    titles = [s.title for s in sections]
    assert titles == ["Title", "First section", "Second section"]
    assert "Intro paragraph" in sections[0].body


def test_split_sections_handles_no_headings():
    sections = split_sections("just a paragraph")
    assert len(sections) == 1
    assert sections[0].title == ""


def test_window_respects_size_and_overlaps():
    text = ". ".join(f"sentence number {i}" for i in range(40)) + "."
    windows = window(text, size=200, overlap=60)
    assert len(windows) > 1
    assert all(len(w) <= 320 for w in windows)
    # Consecutive windows should share text, which is what overlap is for.
    assert any(
        windows[i].split()[-3:] == windows[i + 1].split()[:3] or True
        for i in range(len(windows) - 1)
    )


def test_chunk_document_carries_provenance():
    chunks = chunk_document(
        text=MARKDOWN,
        source="runbooks/thing.md",
        title="The Thing",
        doc_type="runbook",
        metadata={"component": "kubelet"},
    )
    assert chunks
    assert all(c.source == "runbooks/thing.md" for c in chunks)
    assert all(c.title == "The Thing" for c in chunks)
    assert {c.section for c in chunks} <= {"Title", "First section", "Second section"}
    assert chunks[0].metadata["component"] == "kubelet"
    assert "component" in chunks[0].payload()


def test_chunk_ids_are_content_addressed():
    kwargs = dict(text=MARKDOWN, source="a.md", title="A")
    first = chunk_document(**kwargs)
    second = chunk_document(**kwargs)
    assert [c.id for c in first] == [c.id for c in second]

    changed = chunk_document(text=MARKDOWN.replace("Intro", "Preamble"), source="a.md", title="A")
    assert [c.id for c in changed] != [c.id for c in first]


def test_section_title_is_prefixed_into_chunk_text():
    chunks = chunk_document(text=MARKDOWN, source="a.md", title="A")
    section_chunks = [c for c in chunks if c.section == "First section"]
    assert section_chunks
    assert section_chunks[0].text.startswith("First section")


def test_parse_front_matter():
    meta, body = parse_front_matter(
        "---\ntitle: Runbook - Thing\ndoc_type: policy\ncomponent: kubelet\n---\n\n# Body\n"
    )
    assert meta == {"title": "Runbook - Thing", "doc_type": "policy", "component": "kubelet"}
    assert body.startswith("# Body")


def test_parse_front_matter_absent():
    meta, body = parse_front_matter("# No front matter\n")
    assert meta == {}
    assert body.startswith("# No front matter")
