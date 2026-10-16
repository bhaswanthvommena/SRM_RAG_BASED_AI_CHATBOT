from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.config import get_settings
from ingestion.discover import read_manifest
from ingestion.fetch import load_raw_html


def main() -> None:
    s = get_settings()
    url = [i for i in read_manifest(s.manifest_path) if i.doc_type == "programme"][0].url
    html = load_raw_html(s, url) or ""
    print("url", url, "html_len", len(html))
    for kw in [
        "eligibility",
        "duration",
        "curriculum",
        "tuition",
        "semester",
        "credit",
        "overview",
        "Bachelor",
        "Industrial",
        "acf",
    ]:
        print(kw, html.lower().count(kw.lower()))

    widgets = sorted(set(re.findall(r'data-widget_type="([^"]+)"', html)))
    print("widgets", widgets[:50])

    for pat in [
        r'"eligibility"\s*:\s*"',
        r"programme_overview",
        r"elementor-element-[a-z0-9]+",
    ]:
        m = re.search(pat, html, re.I)
        print("match", pat, bool(m))

    # Sample a known content page instead
    pages = [i for i in read_manifest(s.manifest_path) if "/fees/" in i.url or i.url.rstrip("/").endswith("/fees")]
    if pages:
        fees_html = load_raw_html(s, pages[0].url)
        if fees_html:
            print("fees page cached", len(fees_html), "scholarship mentions", fees_html.lower().count("scholarship"))


if __name__ == "__main__":
    main()
