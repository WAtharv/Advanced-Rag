"""HTML document extractor using beautifulsoup4 with boilerplate stripping and structure preservation."""

from __future__ import annotations

import logging
from pathlib import Path

from bs4 import BeautifulSoup

from advanced_rag.ingestion.chunker import Section
from advanced_rag.ingestion.extractors.base import BaseExtractor, Document
from advanced_rag.ingestion.extractors.cleaner import normalize_text

logger = logging.getLogger(__name__)

_TAGS_TO_REMOVE = {
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "noscript",
    "svg",
    "iframe",
}


def _extract_canonical_url(soup: BeautifulSoup) -> str | None:
    """Extract canonical or OpenGraph URL from HTML metadata."""
    canonical = soup.find("link", rel=lambda r: r and "canonical" in r.lower())
    if canonical and canonical.get("href"):
        return canonical["href"].strip()
    og_url = soup.find("meta", property="og:url")
    if og_url and og_url.get("content"):
        return og_url["content"].strip()
    return None


class HtmlExtractor(BaseExtractor):
    """Extracts clean readable text from HTML documents, removing boilerplate while preserving hierarchy."""

    def extract(self, path: Path, *, root: Path | None = None) -> Document:
        source = path.relative_to(root).as_posix() if root else path.name
        title = path.stem.replace("-", " ").replace("_", " ").title()

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"HTML file is empty or does not exist: {source}")

        raw_bytes = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")

        soup = BeautifulSoup(text, "html.parser")

        # Extract source URL metadata before decomposing head elements
        canonical_url = _extract_canonical_url(soup)

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
                    sec_body = normalize_text("\n\n".join(current_content), preserve_code_blocks=True)
                    if sec_body:
                        sections.append(
                            Section(
                                title=current_title,
                                body=sec_body,
                                metadata={"file_type": "html", "extraction_format": "html"},
                            )
                        )
                    current_content = []
                if heading_text:
                    current_title = heading_text
            elif tag_name == "table":
                rows_text: list[str] = []
                all_trs = element.find_all("tr")
                for tr_idx, tr in enumerate(all_trs):
                    cells = [
                        td.get_text(separator=" ", strip=True).replace("\n", " ").replace("|", "\\|")
                        for td in tr.find_all(["td", "th"])
                    ]
                    if any(cells):
                        rows_text.append("| " + " | ".join(cells) + " |")
                        if tr_idx == 0 and len(all_trs) > 1:
                            rows_text.append("| " + " | ".join(["---"] * len(cells)) + " |")
                if rows_text:
                    current_content.append("\n".join(rows_text))
            elif tag_name in ("ul", "ol"):
                items = [li.get_text(separator=" ", strip=True) for li in element.find_all("li", recursive=False)]
                bullet_list = [f"- {it}" for it in items if it]
                if bullet_list:
                    current_content.append("\n".join(bullet_list))
            elif tag_name in ("pre", "code"):
                code_text = element.get_text()
                if code_text.strip():
                    current_content.append(f"```\n{code_text.strip()}\n```")
            else:
                p_text = element.get_text(separator=" ", strip=True)
                if p_text:
                    current_content.append(p_text)

        if current_content:
            sec_body = normalize_text("\n\n".join(current_content), preserve_code_blocks=True)
            if sec_body:
                sections.append(
                    Section(
                        title=current_title,
                        body=sec_body,
                        metadata={"file_type": "html", "extraction_format": "html"},
                    )
                )

        if not sections:
            all_text = normalize_text(soup.get_text(separator="\n", strip=True), preserve_code_blocks=True)
            if not all_text:
                raise ValueError(f"HTML file contains no readable text: {source}")
            sections.append(
                Section(
                    title="Overview",
                    body=all_text,
                    metadata={"file_type": "html", "extraction_format": "html"},
                )
            )

        body = "\n\n".join(f"## {s.title}\n\n{s.body}" if s.title else s.body for s in sections)
        doc_meta: dict[str, str] = {
            "file_type": "html",
            "extraction_format": "html",
        }
        if canonical_url:
            doc_meta["source_url"] = canonical_url

        return Document(
            source=source,
            title=title,
            body=body,
            doc_type="html",
            metadata=doc_meta,
            sections=sections,
            file_type="html",
            source_url=canonical_url,
        )
