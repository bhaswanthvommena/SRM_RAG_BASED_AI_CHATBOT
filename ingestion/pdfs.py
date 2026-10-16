from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import orjson
from rich.console import Console

from ingestion.config import Settings, get_settings
from ingestion.http_client import CrawlClient
from ingestion.models import ParsedDocument
from ingestion.parse import content_hash, load_parsed, normalize_text, parsed_path

console = Console()

# Prefer PDFs linked from these doc types / URL paths
PRIORITY_PATH_HINTS = (
    "/fees",
    "/admission",
    "/hostel",
    "/calendar",
    "/scholarship",
    "/tuition",
    "/curriculum",
    "/regulation",
)


def pdf_cache_path(settings: Settings, url: str) -> Path:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    name = Path(urlparse(url).path).name or "document.pdf"
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:80]
    return settings.pdf_dir / f"{key}_{safe}"


def extract_pdf_text(path: Path) -> str:
    text_parts: list[str] = []
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            for page in doc:
                text_parts.append(page.get_text("text"))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]pymupdf failed[/] {path.name}: {exc}")

    text = normalize_text("\n".join(text_parts))
    if len(text) >= 100:
        return text

    # Fallback / table-friendly
    try:
        import pdfplumber

        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                parts.append(t)
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        cells = [c or "" for c in row]
                        parts.append(" | ".join(cells))
        return normalize_text("\n".join(parts))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]pdfplumber failed[/] {path.name}: {exc}")
        return text


def collect_pdf_urls(settings: Settings, *, limit: int | None = None) -> list[tuple[str, str]]:
    """Return list of (pdf_url, source_page_url) from parsed docs."""
    settings.parsed_dir.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in sorted(settings.parsed_dir.glob("*.json")):
        data = orjson.loads(path.read_bytes())
        page_url = data.get("url") or ""
        priority = any(h in page_url.lower() for h in PRIORITY_PATH_HINTS)
        for pdf_url in data.get("pdf_links") or []:
            if pdf_url in seen:
                continue
            seen.add(pdf_url)
            # Priority pages first: prepend
            if priority:
                pairs.insert(0, (pdf_url, page_url))
            else:
                pairs.append((pdf_url, page_url))
    if limit is not None:
        pairs = pairs[:limit]
    return pairs


async def run_pdf_ingest(
    settings: Settings | None = None,
    *,
    limit: int | None = 50,
) -> list[ParsedDocument]:
    settings = settings or get_settings()
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    pairs = collect_pdf_urls(settings, limit=limit)
    console.print(f"[cyan]PDF candidates[/] {len(pairs)}")

    docs: list[ParsedDocument] = []
    async with CrawlClient(settings) as client:
        for pdf_url, source_url in pairs:
            dest = pdf_cache_path(settings, pdf_url)
            try:
                if not dest.exists():
                    resp = await client.get(pdf_url)
                    dest.write_bytes(resp.content)
                text = extract_pdf_text(dest)
                if len(text) < 40:
                    console.print(f"[dim]skip empty pdf[/] {pdf_url}")
                    continue
                title = Path(urlparse(pdf_url).path).name
                doc = ParsedDocument(
                    url=pdf_url,
                    title=title,
                    text=text,
                    doc_type="pdf",
                    wp_type="attachment",
                    source="pdf",
                    content_hash=content_hash(text),
                    structured={"source_page": source_url},
                    sections=[{"heading": "PDF", "text": text}],
                )
                # Save alongside HTML parses
                out = parsed_path(settings, pdf_url)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(orjson.dumps(doc.model_dump(mode="json"), option=orjson.OPT_INDENT_2))
                docs.append(doc)
                console.print(f"[green]pdf[/] {title} ({len(text)} chars)")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]pdf failed[/] {pdf_url}: {exc}")

    console.print(f"[bold green]PDFs ingested[/] {len(docs)}")
    return docs
