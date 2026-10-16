from __future__ import annotations

import asyncio
import gzip
import hashlib
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from tenacity import RetryError

from ingestion.config import Settings, get_settings
from ingestion.discover import read_manifest, write_manifest
from ingestion.http_client import CrawlClient
from ingestion.models import ManifestItem

console = Console()


def url_cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def raw_html_path(settings: Settings, url: str) -> Path:
    return settings.raw_dir / f"{url_cache_key(url)}.html.gz"


def load_raw_html(settings: Settings, url: str) -> str | None:
    path = raw_html_path(settings, url)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        return f.read()


def save_raw_html(settings: Settings, url: str, html: str) -> Path:
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_html_path(settings, url)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(html)
    return path


async def fetch_one(
    client: CrawlClient,
    settings: Settings,
    item: ManifestItem,
    *,
    force: bool = False,
) -> ManifestItem:
    path = raw_html_path(settings, item.url)
    if path.exists() and not force:
        item.status = "fetched"
        return item
    try:
        resp = await client.get(item.url)
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            # Still save; parser can decide
            pass
        save_raw_html(settings, item.url, resp.text)
        item.status = "fetched"
    except RetryError as exc:
        item.status = "error"
        item.extra["error"] = f"retry exhausted: {exc}"
    except Exception as exc:  # noqa: BLE001
        item.status = "error"
        item.extra["error"] = str(exc)
    return item


async def run_fetch(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    force: bool = False,
    doc_types: list[str] | None = None,
) -> list[ManifestItem]:
    settings = settings or get_settings()
    items = read_manifest(settings.manifest_path)
    if not items:
        console.print("[red]No manifest found. Run discover first.[/]")
        return []

    if doc_types:
        allowed = set(doc_types)
        items = [i for i in items if i.doc_type in allowed]

    # Prefer high-value types first
    priority = {
        "programme": 0,
        "page": 1,
        "faculty": 2,
        "placement": 3,
        "news": 4,
        "press_release": 5,
        "staff": 6,
        "leadership": 7,
    }
    items.sort(key=lambda i: (priority.get(i.doc_type, 99), i.url))
    if limit is not None:
        items = items[:limit]

    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(settings.crawl_concurrency)
    updated: list[ManifestItem] = []

    async with CrawlClient(settings) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching", total=len(items))

            async def worker(item: ManifestItem) -> ManifestItem:
                async with sem:
                    result = await fetch_one(client, settings, item, force=force)
                    progress.advance(task)
                    return result

            results = await asyncio.gather(*[worker(i) for i in items])
            updated = list(results)

    # Merge statuses back into full manifest
    by_url = {i.url: i for i in updated}
    full = read_manifest(settings.manifest_path)
    for i, item in enumerate(full):
        if item.url in by_url:
            full[i] = by_url[item.url]
    write_manifest(settings.manifest_path, full)

    ok = sum(1 for i in updated if i.status == "fetched")
    err = sum(1 for i in updated if i.status == "error")
    console.print(f"[bold green]Fetch done[/] ok={ok} error={err} total={len(updated)}")
    return updated
