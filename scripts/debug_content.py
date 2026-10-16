from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from selectolax.lexbor import LexborHTMLParser

from ingestion.config import get_settings
from ingestion.discover import read_manifest
from ingestion.fetch import load_raw_html
from ingestion.parse import parse_html_document


def inspect(url: str) -> None:
    s = get_settings()
    html = load_raw_html(s, url)
    if not html:
        print("NOT CACHED", url)
        return
    tree = LexborHTMLParser(html)
    shortcodes = []
    for n in tree.css(".elementor-shortcode, .elementor-widget-shortcode, .elementor-widget-text-editor"):
        t = (n.text(strip=True) or "")[:200]
        shortcodes.append((len(n.text(strip=True) or ""), t))
    print("\n===", url)
    print("html", len(html), "shortcode/text widgets", len(shortcodes))
    for sc in sorted(shortcodes, reverse=True)[:5]:
        print(" ", sc[0], sc[1][:150])
    # Find items in manifest
    items = [i for i in read_manifest(s.manifest_path) if i.url == url]
    if items:
        doc = parse_html_document(html, items[0])
        print("parsed len", len(doc.text), "title", doc.title)
        print(doc.text[:500].replace("\n", " | "))


def main() -> None:
    s = get_settings()
    items = read_manifest(s.manifest_path)
    # Prefer specific useful URLs if cached
    wanted = []
    for i in items:
        u = i.url.lower()
        if any(
            x in u
            for x in (
                "/fees/",
                "/admission/our-selection",
                "/hostel/",
                "/faculty/",
                "computer-science",
                "/placement-data/",
            )
        ):
            wanted.append(i.url)
    for url in wanted[:8]:
        inspect(url)


if __name__ == "__main__":
    main()
