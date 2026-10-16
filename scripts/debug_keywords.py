from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from selectolax.lexbor import LexborHTMLParser

from ingestion.config import get_settings
from ingestion.fetch import load_raw_html


def main() -> None:
    s = get_settings()
    url = "https://www.srmap.edu.in/admission/our-selection-process/"
    html = load_raw_html(s, url) or ""
    print("len", len(html))
    for kw in ["SRMJEEE", "Selection", "eligibility", "entrance", "counseling", "counselling", "Apply"]:
        print(kw, html.count(kw), html.lower().count(kw.lower()))

    # Show context around SRMJEEE
    for m in re.finditer(r"SRMJEEE", html):
        start = max(0, m.start() - 100)
        end = min(len(html), m.end() + 200)
        snippet = re.sub(r"<[^>]+>", " ", html[start:end])
        snippet = re.sub(r"\s+", " ", snippet)
        print("CTX:", snippet[:300])
        break

    tree = LexborHTMLParser(html)
    # Largest text-editor widgets
    nodes = tree.css(".elementor-widget-text-editor, .elementor-widget-shortcode, .elementor-widget-heading")
    scored = []
    for n in nodes:
        t = n.text(strip=True) or ""
        if "Programme Finder" in t or "Close About" in t:
            continue
        scored.append((len(t), (n.attributes.get("class") or "")[:60], t[:180]))
    scored.sort(reverse=True)
    print("top non-nav widgets", len(scored))
    for row in scored[:10]:
        print(row[0], row[1], "|", row[2])


if __name__ == "__main__":
    main()
