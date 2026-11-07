"""HTML document extractor using beautifulsoup4."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document

_TAGS_TO_REMOVE = {"script", "style", "nav", "header", "footer", "noscript", "meta", "link"}


class HtmlExtractor(BaseExtractor):
    """Extracts clean readable text from HTML documents, preserving heading hierarchy."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"HTML file is empty or does not exist: {source}")

        raw_bytes = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")

        # Pure in-memory parsing - no external network calls
        soup = BeautifulSoup(text, "html.parser")

        for tag in soup.find_all(list(_TAGS_TO_REMOVE)):
            tag.decompose()

        if soup.title and soup.title.string and soup.title.string.strip():
            title = soup.title.string.strip()

        sections: list[Section] = []
        current_title = "Overview"
        current_content: list[str] = []

        elements = soup.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "ul", "ol", "pre", "code"]
        )

        for element in elements:
            tag_name = element.name.lower()
            if tag_name.startswith("h"):
                heading_text = element.get_text(separator=" ", strip=True)
                if current_content:
                    sections.append(
                        Section(
                            title=current_title,
                            body="\n\n".join(current_content),
                            metadata={"file_type": "html"},
                        )
                    )
                    current_content = []
                if heading_text:
                    current_title = heading_text
            elif tag_name == "table":
                rows_text: list[str] = []
                for tr in element.find_all("tr"):
                    cells = [
                        td.get_text(separator=" ", strip=True) for td in tr.find_all(["td", "th"])
                    ]
                    if any(cells):
                        rows_text.append(" | ".join(cells))
                if rows_text:
                    current_content.append("\n".join(rows_text))
            elif tag_name in ("ul", "ol"):
                items = [li.get_text(separator=" ", strip=True) for li in element.find_all("li")]
                bullet_list = [f"- {it}" for it in items if it]
                if bullet_list:
                    current_content.append("\n".join(bullet_list))
            else:
                p_text = element.get_text(separator=" ", strip=True)
                if p_text:
                    current_content.append(p_text)

        if current_content:
            sections.append(
                Section(
                    title=current_title,
                    body="\n\n".join(current_content),
                    metadata={"file_type": "html"},
                )
            )

        if not sections:
            all_text = soup.get_text(separator="\n", strip=True)
            if not all_text:
                raise ValueError(f"HTML file contains no readable text: {source}")
            sections.append(
                Section(title="Overview", body=all_text, metadata={"file_type": "html"})
            )

        body = "\n\n".join(f"## {s.title}\n\n{s.body}" if s.title else s.body for s in sections)
        return Document(
            source=source,
            title=title,
            body=body,
            doc_type="html",
            metadata={"file_type": "html"},
            sections=sections,
        )

