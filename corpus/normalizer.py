"""
HTML/Markdown Normalizer and Structured Field Extractor for CloudGPT.

Converts raw HTML documentation into clean, structural Markdown with preserved
code blocks, tables, headings, and metadata tags for AWS, Azure, and GCP.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

# Ensure root is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from corpus.manifest import ManifestEntry


@dataclass
class NormalizedDoc:
    title: str
    clean_text: str            # normalized markdown
    heading_tree: list[dict]   # [{level, text, anchor}]
    has_code: bool
    has_table: bool
    version_label: Optional[str]
    word_count: int
    content_hash: str          # sha256(clean_text)


class DocNormalizer:
    """Provider-aware HTML to clean Markdown normalizer."""

    def __init__(self) -> None:
        self._version_re = re.compile(r"\b(v\d+(?:\.\d+)+|version \d+)\b", re.IGNORECASE)

    def normalize(self, raw_html: str, entry: Optional[ManifestEntry] = None, base_url: str = "") -> NormalizedDoc:
        """Parse raw HTML into a structured NormalizedDoc."""
        if not raw_html or not raw_html.strip():
            return NormalizedDoc(
                title="",
                clean_text="",
                heading_tree=[],
                has_code=False,
                has_table=False,
                version_label=None,
                word_count=0,
                content_hash=hashlib.sha256(b"").hexdigest(),
            )

        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Strip non-content and layout elements
        unwanted_selectors = [
            "nav", "footer", "header", "script", "style", "noscript", "svg",
            ".cookie-banner", ".feedback-widget", ".breadcrumb-nav", ".sidebar",
            ".toc", ".metadata", ".page-actions", "#feedback", ".feedback-section",
            ".awsui-tabs", ".azure-feedback", ".devsite-feedback"
        ]
        for sel in unwanted_selectors:
            for tag in soup.select(sel):
                tag.decompose()

        # 2. Extract Document Title
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text().strip()
        elif soup.title:
            title = soup.title.get_text().strip()
        if not title and entry:
            title = entry.title

        # Clean title artifacts
        title = re.sub(r"\s*\|\s*(AWS|Microsoft Learn|Google Cloud|Azure Docs).*$", "", title, flags=re.IGNORECASE).strip()

        # 3. Provider-specific content container extraction
        provider = entry.provider.lower() if entry else self._guess_provider(raw_html, base_url)
        content_root = None

        if provider == "aws":
            content_root = soup.select_one("#main-content") or soup.select_one("#main-col-body") or soup.select_one("main")
        elif provider == "azure":
            content_root = soup.select_one(".content") or soup.select_one("main#main") or soup.select_one("div.mainContainer") or soup.select_one("main")
        elif provider == "gcp":
            content_root = soup.select_one("article.devsite-article") or soup.select_one("div.devsite-article-body") or soup.select_one(".devsite-content") or soup.select_one("main")

        if content_root is None:
            content_root = soup.find("article") or soup.find("main") or soup.body or soup

        # 4. Extract Headings Tree
        heading_tree = []
        for h in content_root.find_all(re.compile(r"^h[1-6]$")):
            lvl = int(h.name[1])
            htext = h.get_text().strip()
            anchor = h.get("id", "")
            if htext:
                heading_tree.append({"level": lvl, "text": htext, "anchor": anchor})

        # 5. Convert HTML elements to Markdown
        clean_markdown = self._convert_to_markdown(content_root)

        # Post-processing: normalize whitespace and consecutive blank lines
        clean_markdown = re.sub(r"\n{3,}", "\n\n", clean_markdown).strip()

        # Prepend main title if missing
        if title and not clean_markdown.startswith(f"# {title}") and not clean_markdown.startswith("# "):
            clean_markdown = f"# {title}\n\n{clean_markdown}"

        has_code = "```" in clean_markdown
        has_table = bool(re.search(r"\|.+\|\n\|[-:\s|]+\|\n\|.+\|", clean_markdown))
        version_match = self._version_re.search(clean_markdown)
        version_label = version_match.group(1) if version_match else None
        word_count = len(clean_markdown.split())
        content_hash = hashlib.sha256(clean_markdown.encode("utf-8")).hexdigest()

        return NormalizedDoc(
            title=title,
            clean_text=clean_markdown,
            heading_tree=heading_tree,
            has_code=has_code,
            has_table=has_table,
            version_label=version_label,
            word_count=word_count,
            content_hash=content_hash,
        )

    def _guess_provider(self, html: str, url: str) -> str:
        if "aws.amazon.com" in url or "docs.aws.amazon.com" in html:
            return "aws"
        if "learn.microsoft.com" in url or "azure" in url:
            return "azure"
        if "cloud.google.com" in url or "google" in url:
            return "gcp"
        return "generic"

    def _convert_to_markdown(self, root) -> str:
        """Convert HTML tags to clean markdown representation."""
        output = []

        for elem in root.children:
            if isinstance(elem, str):
                text = elem.strip()
                if text:
                    output.append(text)
                continue

            tag_name = elem.name.lower() if elem.name else ""

            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag_name[1])
                output.append(f"\n\n{'#' * level} {elem.get_text().strip()}\n\n")

            elif tag_name == "p":
                output.append(f"\n\n{self._inline_formatting(elem)}\n\n")

            elif tag_name in ("pre", "code"):
                code_text = elem.get_text().rstrip()
                lang = ""
                # Attempt to extract code language from class
                classes = elem.get("class", [])
                if isinstance(classes, list):
                    for c in classes:
                        if c.startswith("language-") or c.startswith("lang-"):
                            lang = c.replace("language-", "").replace("lang-", "")
                output.append(f"\n\n```{lang}\n{code_text}\n```\n\n")

            elif tag_name in ("ul", "ol"):
                items = []
                for i, li in enumerate(elem.find_all("li", recursive=False), 1):
                    bullet = f"{i}." if tag_name == "ol" else "-"
                    items.append(f"{bullet} {self._inline_formatting(li).strip()}")
                output.append("\n\n" + "\n".join(items) + "\n\n")

            elif tag_name == "table":
                output.append(self._table_to_markdown(elem))

            elif tag_name in ("div", "section", "article"):
                output.append(self._convert_to_markdown(elem))

            elif tag_name in ("blockquote", "note", "alert"):
                output.append(f"\n\n> {self._inline_formatting(elem).strip()}\n\n")

            else:
                text = self._inline_formatting(elem)
                if text.strip():
                    output.append(text)

        return "".join(output)

    def _inline_formatting(self, elem) -> str:
        """Handle inline formatting (bold, italics, links, inline code)."""
        if isinstance(elem, str):
            return elem

        parts = []
        for child in elem.children:
            if isinstance(child, str):
                parts.append(child)
                continue

            c_tag = child.name.lower() if child.name else ""
            if c_tag in ("strong", "b"):
                parts.append(f"**{child.get_text().strip()}**")
            elif c_tag in ("em", "i"):
                parts.append(f"*{child.get_text().strip()}*")
            elif c_tag == "code":
                parts.append(f"`{child.get_text().strip()}`")
            elif c_tag == "a":
                href = child.get("href", "")
                text = child.get_text().strip() or href
                if href and not href.startswith("#") and not href.startswith("javascript:"):
                    parts.append(f"[{text}]({href})")
                else:
                    parts.append(text)
            else:
                parts.append(self._inline_formatting(child))

        return "".join(parts)

    def _table_to_markdown(self, table_elem) -> str:
        """Convert an HTML table to a clean Markdown table."""
        rows = table_elem.find_all("tr")
        if not rows:
            return ""

        md_rows = []
        col_count = 0

        for r_idx, row in enumerate(rows):
            cols = row.find_all(["th", "td"])
            if not cols:
                continue
            cells = [re.sub(r"\s+", " ", c.get_text().strip().replace("|", "\\|")) for c in cols]
            col_count = max(col_count, len(cells))
            md_rows.append(f"| {' | '.join(cells)} |")

            if r_idx == 0:
                sep = ["---"] * len(cells)
                md_rows.append(f"| {' | '.join(sep)} |")

        if len(md_rows) <= 1:
            return ""

        return "\n\n" + "\n".join(md_rows) + "\n\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudGPT Document Normalizer CLI")
    parser.add_argument("--url", help="URL to fetch and normalize")
    parser.add_argument("--file", help="Path to local HTML file to normalize")
    args = parser.parse_args()

    normalizer = DocNormalizer()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: file not found at {path}")
            sys.exit(1)
        raw_html = path.read_text(encoding="utf-8")
        doc = normalizer.normalize(raw_html)
        print(f"\n--- NORMALIZED: {doc.title} ({doc.word_count} words, hash={doc.content_hash[:8]}) ---")
        print(doc.clean_text[:1500])
        return

    if args.url:
        import httpx
        resp = httpx.get(args.url, follow_redirects=True, timeout=15.0)
        doc = normalizer.normalize(resp.text, base_url=args.url)
        print(f"\n--- NORMALIZED: {doc.title} ({doc.word_count} words, hash={doc.content_hash[:8]}) ---")
        print(doc.clean_text[:1500])
        return

    print("Please provide --url or --file argument.")


if __name__ == "__main__":
    main()
