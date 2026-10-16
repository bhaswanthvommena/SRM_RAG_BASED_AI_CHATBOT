from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from ingestion.config import get_settings

app = typer.Typer(help="SRM AP RAG ingestion CLI", no_args_is_help=True)
console = Console()


@app.command("discover")
def discover_cmd() -> None:
    """Discover URLs via WP REST + sitemaps; write data/manifest.jsonl."""
    from ingestion.discover import run_discover

    asyncio.run(run_discover())


@app.command("fetch")
def fetch_cmd(
    limit: Optional[int] = typer.Option(None, help="Max pages to fetch"),
    force: bool = typer.Option(False, help="Re-download even if cached"),
    doc_type: Optional[list[str]] = typer.Option(None, help="Filter doc_type(s)"),
) -> None:
    """Download HTML pages listed in the manifest into data/raw/."""
    from ingestion.fetch import run_fetch

    asyncio.run(run_fetch(limit=limit, force=force, doc_types=doc_type))


@app.command("parse")
def parse_cmd(
    limit: Optional[int] = typer.Option(None),
    doc_type: Optional[list[str]] = typer.Option(None),
) -> None:
    """Parse cached HTML into data/parsed/*.json."""
    from ingestion.parse import run_parse

    run_parse(limit=limit, doc_types=doc_type)


@app.command("pdfs")
def pdfs_cmd(
    limit: Optional[int] = typer.Option(50, help="Max PDFs to download/extract"),
) -> None:
    """Download and extract PDFs linked from parsed pages."""
    from ingestion.pdfs import run_pdf_ingest

    asyncio.run(run_pdf_ingest(limit=limit))


@app.command("persist")
def persist_cmd(
    limit: Optional[int] = typer.Option(None),
    doc_type: Optional[list[str]] = typer.Option(None, help="Filter doc_type(s)"),
    url_contains: Optional[list[str]] = typer.Option(None, help="Only URLs containing these substrings"),
) -> None:
    """Upsert documents + text chunks to Postgres (NO embedding API calls)."""
    from ingestion.db import run_persist

    run_persist(limit=limit, doc_types=doc_type, url_contains=url_contains)


@app.command("prepare-local-index")
def prepare_local_index_cmd() -> None:
    """
    Switch vector column to BGE 384-d safely.

    Clears incompatible (e.g. Gemini 768-d) embeddings and requeues those chunks.
    Does NOT delete documents or chunk text. Does NOT crawl/fetch/parse/persist.
    """
    from ingestion.db import prepare_local_index
    from ingestion.embeddings import LOCAL_BGE_DIMS, LOCAL_BGE_MODEL_ID

    prepare_local_index(target_dims=LOCAL_BGE_DIMS, target_model=LOCAL_BGE_MODEL_ID)
    console.print(
        "Next: set EMBEDDING_PROVIDER=local and EMBEDDING_DIMS=384 in .env, "
        "then run embed-worker."
    )


@app.command("prepare-gemini-index")
def prepare_gemini_index_cmd() -> None:
    """
    Switch vector column to hosted Gemini embeddings (default 768-d).

    Clears incompatible (e.g. local BGE 384-d) embeddings and requeues those chunks.
    Does NOT delete documents or chunk text. Does NOT crawl/fetch/parse/persist.
    """
    from ingestion.db import prepare_gemini_index

    prepare_gemini_index()
    console.print(
        "Next: set EMBEDDING_PROVIDER=gemini and EMBEDDING_DIMS=768 in .env, "
        "set GEMINI_EMBEDDING_API_KEY, then run embed-worker."
    )


@app.command("embed-worker")
def embed_worker_cmd(
    batch_size: Optional[int] = typer.Option(
        None, help="Chunks per claim/encode batch (default: EMBED_BATCH_SIZE or 8)"
    ),
    max_per_run: Optional[int] = typer.Option(500, help="Stop after N embeddings this run"),
    max_attempts: int = typer.Option(5, help="Max attempts before leaving chunk as failed"),
    pause_on_quota: int = typer.Option(0, help="Sleep N seconds on Gemini quota before exit"),
) -> None:
    """Resume embedding pending chunks (Gemini or local, per EMBEDDING_PROVIDER)."""
    from ingestion.embed_worker import run_embed_worker

    run_embed_worker(
        batch_size=batch_size,
        max_per_run=max_per_run,
        max_attempts=max_attempts,
        pause_on_quota_seconds=pause_on_quota,
    )


@app.command("status")
def status_cmd() -> None:
    """Show crawl + embed queue counts + active embedding provider."""
    from ingestion.embed_worker import print_status

    print_status()


@app.command("load")
def load_cmd(
    limit: Optional[int] = typer.Option(None),
    skip_embed: bool = typer.Option(True, help="Deprecated; load no longer embeds"),
    doc_type: Optional[list[str]] = typer.Option(None, help="Filter doc_type(s)"),
    url_contains: Optional[list[str]] = typer.Option(None, help="Only URLs containing these substrings"),
) -> None:
    """Alias for persist (embedding is handled by embed-worker)."""
    from ingestion.db import run_persist

    console.print("[dim]load → persist (embeddings via embed-worker)[/]")
    run_persist(limit=limit, doc_types=doc_type, url_contains=url_contains)


@app.command("pipeline")
def pipeline_cmd(
    fetch_limit: Optional[int] = typer.Option(None, help="Limit pages fetched"),
    pdf_limit: int = typer.Option(30, help="Max PDFs"),
    persist_limit: Optional[int] = typer.Option(None),
    skip_discover: bool = typer.Option(False),
    run_embed: bool = typer.Option(False, help="Also run embed-worker after persist"),
    embed_max: Optional[int] = typer.Option(200, help="Max embeds if --run-embed"),
) -> None:
    """Crawl path only by default: discover → fetch → parse → pdfs → persist."""
    from ingestion.db import run_persist
    from ingestion.discover import run_discover
    from ingestion.embed_worker import run_embed_worker
    from ingestion.fetch import run_fetch
    from ingestion.parse import run_parse
    from ingestion.pdfs import run_pdf_ingest

    settings = get_settings()
    if not skip_discover:
        asyncio.run(run_discover(settings))
    asyncio.run(run_fetch(settings, limit=fetch_limit))
    run_parse(settings)
    asyncio.run(run_pdf_ingest(settings, limit=pdf_limit))
    run_persist(settings, limit=persist_limit)
    if run_embed:
        run_embed_worker(settings, max_per_run=embed_max)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
