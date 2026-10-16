from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import orjson
import trafilatura
from rich.console import Console
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ingestion.config import Settings, get_settings
from ingestion.discover import read_manifest
from ingestion.fetch import load_raw_html
from ingestion.models import ManifestItem, ParsedDocument

console = Console()

REMOVE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "nav",
    "header",
    "footer",
    "aside",
    ".elementor-location-header",
    ".elementor-location-footer",
    ".elementor-location-popup",
    ".elementor-nav-menu",
    ".elementor-nav-menu--main",
    ".elementor-nav-menu--dropdown",
    ".elementor-widget-nav-menu",
    ".elementor-widget-header",
    "#masthead",
    "#colophon",
    ".site-header",
    ".site-footer",
    ".menu",
    ".navbar",
    ".breadcrumb",
    ".breadcrumbs",
    ".social-icons",
    ".share",
    ".cookie",
    "#wpadminbar",
    ".mobile-menu",
    ".main-navigation",
    ".top-bar",
    ".offcanvas",
]

# Class/id tokens that usually mean chrome, not page body
CHROME_TOKEN_RE = re.compile(
    r"(?:^|[\s_-])(nav(?:bar|igation)?|menu|footer|sidebar|breadcrumb|offcanvas|popup|modal|cookie|social)(?:$|[\s_-])",
    re.I,
)

NAV_MARKERS = (
    "Programme Finder",
    "Admissions Open",
    "Helpline -",
    "Close About SRMAP",
    "Open About SRMAP",
    "Students\nParents\nAlumni",
)

PDF_HREF_RE = re.compile(r"\.pdf($|\?)", re.I)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
WS_RE = re.compile(r"[ \t]+\n")
MULTI_NL_RE = re.compile(r"\n{3,}")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = WS_RE.sub("\n", text)
    text = MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def table_to_markdown(node) -> str:
    rows: list[list[str]] = []
    for tr in node.css("tr"):
        cells = [c.text(strip=True) for c in tr.css("th, td")]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    header = norm[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in norm[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def extract_pdf_links(tree: HTMLParser, page_url: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if not PDF_HREF_RE.search(href):
            continue
        abs_url = urljoin(page_url, href)
        if abs_url not in seen:
            seen.add(abs_url)
            links.append(abs_url)
    return links


def _looks_like_chrome(node) -> bool:
    attrs = " ".join(
        filter(
            None,
            [
                node.attributes.get("id"),
                node.attributes.get("class"),
                node.attributes.get("role"),
            ],
        )
    )
    return bool(attrs and CHROME_TOKEN_RE.search(attrs))


def strip_boilerplate(html: str) -> HTMLParser:
    tree = HTMLParser(html)
    for sel in REMOVE_SELECTORS:
        for node in tree.css(sel):
            node.decompose()
    # Second pass: remove remaining chrome-ish containers
    root = tree.body or tree
    for node in list(root.iter()):
        if node.tag in ("div", "section", "ul", "aside") and _looks_like_chrome(node):
            # Avoid deleting huge content wrappers that happen to include "header" in class
            text_len = len(node.text(strip=True) or "")
            if text_len < 8000:
                node.decompose()
    return tree


def extract_main_html(tree: HTMLParser) -> str:
    selectors = (
        "article",
        "main",
        ".entry-content",
        ".elementor-widget-theme-post-content",
        ".elementor-widget-text-editor",
        ".programme-content",
        ".faculty-content",
        "#content",
        ".site-content",
        ".page-content",
    )
    candidates = []
    for sel in selectors:
        for node in tree.css(sel):
            tlen = len(node.text(strip=True) or "")
            if tlen > 80:
                # Penalize nodes that still look like chrome
                score = tlen
                if _looks_like_chrome(node):
                    score *= 0.2
                candidates.append((score, node))
    if candidates:
        best = max(candidates, key=lambda x: x[0])[1]
        return best.html or ""
    body = tree.body
    return body.html if body is not None else tree.html or ""


def is_nav_polluted(text: str) -> bool:
    if text.count("Programme Finder") >= 2:
        return True
    hits = sum(1 for m in NAV_MARKERS if m in text)
    return hits >= 3


MENU_LINE_EXACT = {
    "students",
    "parents",
    "alumni",
    "faculty & staff",
    "careers",
    "qar",
    "programme finder",
    "admissions open 2026",
    "about srmap",
    "close about srmap",
    "open about srmap",
    "facebook",
    "instagram",
    "youtube",
    "linkedin",
    "x-twitter",
    "home",
}


def strip_menu_lines(text: str) -> str:
    """Remove obvious mega-menu / chrome lines while keeping real paragraphs."""
    kept: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            kept.append("")
            continue
        low = raw.lower()
        if low in MENU_LINE_EXACT:
            continue
        if low.startswith("helpline -"):
            continue
        # Skip ultra-short nav crumbs unless they look like data
        if len(raw) <= 28 and " " not in raw and not any(ch.isdigit() for ch in raw):
            if low in {
                "about",
                "admissions",
                "academics",
                "research",
                "placements",
                "overview",
                "contact",
                "apply",
            }:
                continue
        kept.append(raw)
    return normalize_text("\n".join(kept))


def clean_nav_residue(text: str) -> str:
    """Drop leading mega-menu residue when present."""
    text = strip_menu_lines(text)
    if not is_nav_polluted(text):
        return text
    cut_markers = (
        "Give wings to your dreams",
        "Eligibility",
        "Duration",
        "Curriculum",
        "About the Programme",
        "Programme Overview",
        "Fee Structure",
        "Our Selection Process",
        "Educational Details",
        "Research Interests",
        "Publications",
    )
    best = -1
    for m in cut_markers:
        idx = text.find(m)
        if idx > best:
            best = idx
    if best > 120:
        return normalize_text(strip_menu_lines(text[best:]))
    return text



def html_to_text_with_structure(html_fragment: str) -> tuple[str, list[dict[str, Any]]]:
    """Convert main HTML to readable text + section list."""
    tree = HTMLParser(html_fragment)
    # Replace tables with markdown in-place via text assembly
    parts: list[str] = []
    sections: list[dict[str, Any]] = []
    current_heading = "Body"
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts
        text = normalize_text("\n".join(current_parts))
        if text:
            sections.append({"heading": current_heading, "text": text})
            parts.append(f"## {current_heading}\n{text}" if current_heading != "Body" else text)
        current_parts = []

    root = tree.body or tree
    for node in root.iter():
        if node.tag in ("h1", "h2", "h3", "h4"):
            flush()
            current_heading = node.text(strip=True) or current_heading
        elif node.tag == "table":
            md = table_to_markdown(node)
            if md:
                current_parts.append(md)
        elif node.tag in ("p", "li", "td", "th", "div", "span"):
            # Only take leaf-ish text to avoid massive duplication
            if node.tag in ("p", "li"):
                t = node.text(strip=True)
                if t and len(t) > 1:
                    prefix = "- " if node.tag == "li" else ""
                    current_parts.append(prefix + t)
        elif node.tag in ("summary", "button") and "accordion" in (node.attributes.get("class") or ""):
            t = node.text(strip=True)
            if t:
                current_parts.append(f"**{t}**")

    flush()
    full = normalize_text("\n\n".join(parts))
    return full, sections


def trafilatura_fallback(html: str, url: str) -> str:
    extracted = trafilatura.extract(
        html,
        url=url,
        include_tables=True,
        include_comments=False,
        include_links=False,
        favor_recall=True,
        output_format="txt",
    )
    return normalize_text(extracted or "")


def parse_faculty_structured(text: str, title: str | None) -> dict[str, Any]:
    emails = EMAIL_RE.findall(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    designation = None
    department = None
    research: list[str] = []
    for i, ln in enumerate(lines[:30]):
        low = ln.lower()
        if any(k in low for k in ("professor", "assistant", "associate", "lecturer", "dean", "director")):
            if designation is None and len(ln) < 120:
                designation = ln
        if "department" in low and department is None:
            department = ln
        if "research" in low and i + 1 < len(lines):
            research.append(lines[i + 1])
    return {
        "name": title,
        "designation": designation,
        "department": department,
        "email": emails[0] if emails else None,
        "research_areas": research[:10],
    }


def parse_programme_structured(text: str, title: str | None) -> dict[str, Any]:
    eligibility = duration = fee = None
    low = text.lower()
    for label, key in (
        ("eligibility", "eligibility"),
        ("duration", "duration"),
        ("fee", "fee_summary"),
        ("tuition", "fee_summary"),
    ):
        idx = low.find(label)
        if idx >= 0:
            snippet = text[idx : idx + 400]
            if key == "eligibility" and eligibility is None:
                eligibility = snippet
            elif key == "duration" and duration is None:
                duration = snippet.split("\n", 1)[0][:200]
            elif key == "fee_summary" and fee is None:
                fee = snippet.split("\n", 1)[0][:200]
    level = "other"
    t = (title or "").lower()
    if any(x in t for x in ("b.tech", "b.sc", "b.a", "bba", "undergraduate", "btech")):
        level = "ug"
    elif any(x in t for x in ("m.tech", "m.sc", "mba", "m.a", "postgraduate")):
        level = "pg"
    elif "ph.d" in t or "phd" in t:
        level = "phd"
    return {
        "name": title,
        "level": level,
        "eligibility": eligibility,
        "duration": duration,
        "fee_summary": fee,
    }


def parse_html_document(html: str, item: ManifestItem) -> ParsedDocument:
    tree = strip_boilerplate(html)
    title = item.title
    if not title:
        h1 = tree.css_first("h1")
        if h1:
            title = h1.text(strip=True)
        else:
            tnode = tree.css_first("title")
            title = tnode.text(strip=True) if tnode else None

    main_html = extract_main_html(tree)
    text, sections = html_to_text_with_structure(main_html)
    text = clean_nav_residue(text)

    # Prefer trafilatura when short or still nav-polluted
    if len(text) < 200 or is_nav_polluted(text):
        traf = trafilatura_fallback(html, item.url)
        traf = clean_nav_residue(traf)
        if len(traf) > 80 and (not is_nav_polluted(traf) or len(traf) < len(text)):
            text = traf
            sections = [{"heading": "Body", "text": text}]

    # Last resort: keep longest non-empty text
    if len(text) < 40:
        text = trafilatura_fallback(html, item.url)
        sections = [{"heading": "Body", "text": text}] if text else []

    pdf_links = extract_pdf_links(HTMLParser(html), item.url)

    structured: dict[str, Any] = {}
    if item.doc_type == "faculty":
        structured = parse_faculty_structured(text, title)
    elif item.doc_type == "programme":
        structured = parse_programme_structured(text, title)
    elif item.doc_type in ("news", "press_release"):
        structured = {"date": item.published.isoformat() if item.published else None}

    # Elementor shells (esp. programmes) often have almost no body copy.
    # Keep a compact title stub rather than indexing the mega-menu.
    if is_nav_polluted(text) or (item.doc_type == "programme" and len(text) < 800):
        stub = normalize_text(
            f"{title or 'Untitled'}\nType: {item.doc_type}\nURL: {item.url}\n"
            f"School: {item.school or ''}\n"
            "Note: Full programme details may live on related school/department pages or PDFs."
        )
        structured = {**structured, "thin_shell": True}
        text = stub
        sections = [{"heading": "Stub", "text": stub}]

    return ParsedDocument(
        url=item.url,
        title=title,
        text=text,
        doc_type=item.doc_type,
        wp_type=item.wp_type,
        wp_id=item.wp_id,
        school=item.school,
        department=item.department or structured.get("department"),
        published_at=item.published,
        modified_at=item.modified,
        is_stale=item.is_stale,
        source="html",
        content_hash=content_hash(text),
        pdf_links=pdf_links,
        structured=structured,
        sections=sections,
    )


def parsed_path(settings: Settings, url: str) -> Path:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return settings.parsed_dir / f"{key}.json"


def save_parsed(settings: Settings, doc: ParsedDocument) -> Path:
    settings.parsed_dir.mkdir(parents=True, exist_ok=True)
    path = parsed_path(settings, doc.url)
    path.write_bytes(orjson.dumps(doc.model_dump(mode="json"), option=orjson.OPT_INDENT_2))
    return path


def load_parsed(settings: Settings, url: str) -> ParsedDocument | None:
    path = parsed_path(settings, url)
    if not path.exists():
        return None
    return ParsedDocument.model_validate(orjson.loads(path.read_bytes()))


def run_parse(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    doc_types: list[str] | None = None,
) -> list[ParsedDocument]:
    settings = settings or get_settings()
    items = read_manifest(settings.manifest_path)
    if doc_types:
        allowed = set(doc_types)
        items = [i for i in items if i.doc_type in allowed]

    # Only parse those that have cached HTML
    ready: list[ManifestItem] = []
    for item in items:
        if load_raw_html(settings, item.url) is not None:
            ready.append(item)
    if limit is not None:
        ready = ready[:limit]

    docs: list[ParsedDocument] = []
    empty = 0
    for item in ready:
        html = load_raw_html(settings, item.url)
        if not html:
            continue
        try:
            doc = parse_html_document(html, item)
            if len(doc.text) < 40:
                empty += 1
            save_parsed(settings, doc)
            docs.append(doc)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]parse failed[/] {item.url}: {exc}")

    console.print(
        f"[bold green]Parsed[/] {len(docs)} documents "
        f"({empty} nearly-empty). Output: {settings.parsed_dir}"
    )
    return docs
