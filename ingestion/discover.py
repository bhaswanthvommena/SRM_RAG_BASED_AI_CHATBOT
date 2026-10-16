from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import orjson
from rich.console import Console

from ingestion.config import Settings, get_settings
from ingestion.http_client import CrawlClient
from ingestion.models import (
    JUNK_URL_SUBSTRINGS,
    MVP_REST_TYPES,
    REST_TO_DOC_TYPE,
    TAXONOMY_SITEMAP_HINTS,
    ManifestItem,
)

console = Console()
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
STALE_YEAR_RE = re.compile(r"(?:^|[^0-9])(20(?:1[0-9]|2[0-2]))(?:[^0-9]|$)")


def _parse_wp_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # WP returns "2026-10-14T12:10:30" without tz; treat as local/naive UTC-ish
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_junk_url(url: str) -> bool:
    lower = url.lower()
    return any(s in lower for s in JUNK_URL_SUBSTRINGS)


def is_taxonomy_sitemap(loc: str) -> bool:
    return any(h in loc for h in TAXONOMY_SITEMAP_HINTS)


def infer_stale(url: str, title: str | None, modified: datetime | None) -> bool:
    hay = f"{url} {title or ''}"
    if STALE_YEAR_RE.search(hay):
        return True
    if modified and modified.year <= 2022:
        return True
    return False


def school_from_url(url: str) -> str | None:
    path = urlparse(url).path.strip("/").split("/")
    if not path:
        return None
    first = path[0].lower()
    mapping = {
        "seas": "School of Engineering & Sciences",
        "esla": "Easwari School of Liberal Arts",
        "psb": "Paari School of Business",
        "slabs": "School of Liberal Arts & Basic Sciences",
        "deeps": "DEEPS",
        "iks": "IKS",
        "aiti": "AI Technology Institute",
        "quti": "Quantum Technology Institute",
    }
    return mapping.get(first)


async def fetch_sitemap_locs(client: CrawlClient, sitemap_url: str) -> list[str]:
    resp = await client.get(sitemap_url)
    root = ET.fromstring(resp.text)
    locs: list[str] = []
    # sitemap index
    for el in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
        if el.text:
            locs.append(el.text.strip())
    # url set
    for el in root.findall("sm:url/sm:loc", SITEMAP_NS):
        if el.text:
            locs.append(el.text.strip())
    # fallback without namespace
    if not locs:
        for el in root.iter():
            if el.tag.endswith("loc") and el.text:
                locs.append(el.text.strip())
    return locs


async def discover_from_sitemaps(client: CrawlClient, settings: Settings) -> dict[str, ManifestItem]:
    index_url = f"{settings.site_base_url.rstrip('/')}/sitemap_index.xml"
    console.print(f"[cyan]Fetching sitemap index[/] {index_url}")
    child_sitemaps = await fetch_sitemap_locs(client, index_url)
    items: dict[str, ManifestItem] = {}

    for sm_url in child_sitemaps:
        if is_taxonomy_sitemap(sm_url):
            console.print(f"  [dim]skip taxonomy sitemap[/] {sm_url}")
            continue
        console.print(f"  [green]sitemap[/] {sm_url}")
        try:
            urls = await fetch_sitemap_locs(client, sm_url)
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]failed[/] {sm_url}: {exc}")
            continue

        # Infer type from sitemap name
        name = sm_url.rsplit("/", 1)[-1]
        doc_type = "page"
        wp_type = "pages"
        if "faculty" in name:
            doc_type, wp_type = "faculty", "faculty-profile"
        elif "news" in name:
            doc_type, wp_type = "news", "news"
        elif "press" in name:
            doc_type, wp_type = "press_release", "press-releases"
        elif "placement" in name:
            doc_type, wp_type = "placement", "placement-statistic"
        elif "conference" in name:
            doc_type, wp_type = "conference", "conference"
        elif "thought" in name:
            doc_type, wp_type = "thought_leadership", "thought-leadership"
        elif "mediaroom" in name:
            doc_type, wp_type = "mediaroom", "mediaroom"

        for url in urls:
            if is_junk_url(url):
                continue
            if url in items:
                continue
            items[url] = ManifestItem(
                url=url,
                wp_type=wp_type,
                doc_type=doc_type,
                school=school_from_url(url),
                is_stale=infer_stale(url, None, None),
                source="sitemap",
            )
    return items


async def discover_rest_type(
    client: CrawlClient,
    settings: Settings,
    rest_base: str,
) -> list[ManifestItem]:
    base = f"{settings.site_base_url.rstrip('/')}/wp-json/wp/v2/{rest_base}"
    page = 1
    per_page = 100
    out: list[ManifestItem] = []
    doc_type = REST_TO_DOC_TYPE.get(rest_base, rest_base)

    while True:
        resp = await client.get(
            base,
            params={
                "page": page,
                "per_page": per_page,
                "_fields": "id,link,title,modified,date,slug,parent",
            },
        )
        data = resp.json()
        if not data:
            break
        total_pages = int(resp.headers.get("X-WP-TotalPages", page))
        for row in data:
            url = row.get("link") or ""
            if not url or is_junk_url(url):
                continue
            title = None
            t = row.get("title")
            if isinstance(t, dict):
                title = t.get("rendered")
            elif isinstance(t, str):
                title = t
            modified = _parse_wp_datetime(row.get("modified"))
            published = _parse_wp_datetime(row.get("date"))
            out.append(
                ManifestItem(
                    url=url,
                    wp_type=rest_base,
                    wp_id=row.get("id"),
                    title=title,
                    modified=modified,
                    published=published,
                    school=school_from_url(url),
                    doc_type=doc_type,
                    is_stale=infer_stale(url, title, modified),
                    source="rest",
                    extra={"slug": row.get("slug"), "parent": row.get("parent")},
                )
            )
        console.print(
            f"  [green]{rest_base}[/] page {page}/{total_pages} (+{len(data)}, total {len(out)})"
        )
        if page >= total_pages:
            break
        page += 1
    return out


async def discover_from_rest(client: CrawlClient, settings: Settings) -> dict[str, ManifestItem]:
    items: dict[str, ManifestItem] = {}
    for rest_base in MVP_REST_TYPES:
        console.print(f"[cyan]REST[/] {rest_base}")
        try:
            rows = await discover_rest_type(client, settings, rest_base)
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]failed {rest_base}[/]: {exc}")
            continue
        for item in rows:
            # REST wins over sitemap (richer metadata)
            items[item.url] = item
    return items


def merge_manifests(
    sitemap_items: dict[str, ManifestItem],
    rest_items: dict[str, ManifestItem],
) -> list[ManifestItem]:
    merged: dict[str, ManifestItem] = dict(sitemap_items)
    for url, item in rest_items.items():
        existing = merged.get(url)
        if existing is None:
            merged[url] = item
            continue
        # Prefer REST fields
        existing.wp_id = item.wp_id or existing.wp_id
        existing.title = item.title or existing.title
        existing.modified = item.modified or existing.modified
        existing.published = item.published or existing.published
        existing.wp_type = item.wp_type or existing.wp_type
        existing.doc_type = item.doc_type or existing.doc_type
        existing.is_stale = item.is_stale or existing.is_stale
        existing.source = "rest"
        existing.extra = {**existing.extra, **item.extra}
        merged[url] = existing
    return sorted(merged.values(), key=lambda x: x.url)


def write_manifest(path: Path, items: list[ManifestItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        for item in items:
            f.write(orjson.dumps(item.model_dump(mode="json")))
            f.write(b"\n")


def read_manifest(path: Path) -> list[ManifestItem]:
    if not path.exists():
        return []
    items: list[ManifestItem] = []
    with path.open("rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(ManifestItem.model_validate(orjson.loads(line)))
    return items


async def run_discover(settings: Settings | None = None) -> list[ManifestItem]:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    async with CrawlClient(settings) as client:
        rest_items = await discover_from_rest(client, settings)
        # Sitemap fills gaps (e.g. URLs not in MVP REST types we already have)
        sitemap_items = await discover_from_sitemaps(client, settings)

    # Keep only MVP doc types from sitemap merge; drop deferred types unless already in REST
    mvp_doc_types = set(REST_TO_DOC_TYPE.values())
    filtered_sitemap = {
        u: i
        for u, i in sitemap_items.items()
        if i.doc_type in mvp_doc_types or u in rest_items
    }

    items = merge_manifests(filtered_sitemap, rest_items)
    write_manifest(settings.manifest_path, items)

    by_type: dict[str, int] = {}
    stale = 0
    for i in items:
        by_type[i.doc_type] = by_type.get(i.doc_type, 0) + 1
        if i.is_stale:
            stale += 1

    console.print(f"\n[bold green]Manifest written[/] {settings.manifest_path}")
    console.print(f"Total URLs: {len(items)}  stale-tagged: {stale}")
    for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
        console.print(f"  {k:20s} {v}")
    return items
