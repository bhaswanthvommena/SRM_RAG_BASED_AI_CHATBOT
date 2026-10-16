from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from pgvector import Vector
from rich.console import Console

from ingestion.config import Settings, get_settings
from ingestion.db import apply_embed_queue_migration, connect, get_vector_column_dims
from ingestion.embeddings import (
    active_dims,
    active_model_id,
    get_embedder,
    is_quota_exhausted,
    is_rate_limit_error,
)

console = Console()


@dataclass
class WorkerResult:
    claimed: int = 0
    completed: int = 0
    failed: int = 0
    deferred: int = 0
    stopped_reason: str | None = None
    provider: str | None = None
    model_id: str | None = None


def _recover_stale_processing(conn, *, stale_minutes: int = 30) -> int:
    row = conn.execute(
        """
        UPDATE chunks
        SET embed_status = 'pending',
            embed_locked_at = NULL,
            embed_error = COALESCE(embed_error, 'recovered stale processing lock')
        WHERE embed_status = 'processing'
          AND (embed_locked_at IS NULL OR embed_locked_at < NOW() - make_interval(mins => %s))
        """,
        (stale_minutes,),
    )
    return row.rowcount if row is not None else 0


def claim_chunks(conn, *, batch_size: int, max_attempts: int) -> list[tuple[int, str]]:
    """Atomically claim pending/failed chunks for embedding."""
    rows = conn.execute(
        """
        WITH cte AS (
            SELECT id
            FROM chunks
            WHERE embed_status IN ('pending', 'failed')
              AND embed_attempts < %s
              AND (embedding IS NULL OR embed_status <> 'completed')
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE chunks c
        SET embed_status = 'processing',
            embed_locked_at = NOW(),
            embed_attempts = c.embed_attempts + 1,
            embed_error = NULL
        FROM cte
        WHERE c.id = cte.id
        RETURNING c.id, c.text
        """,
        (max_attempts, batch_size),
    ).fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def mark_completed(
    conn,
    chunk_id: int,
    embedding: list[float],
    *,
    model_id: str,
) -> None:
    conn.execute(
        """
        UPDATE chunks
        SET embedding = %s,
            embed_status = 'completed',
            embed_model = %s,
            embedded_at = NOW(),
            embed_locked_at = NULL,
            embed_error = NULL
        WHERE id = %s
        """,
        (Vector(embedding), model_id, chunk_id),
    )


def mark_failed(conn, chunk_id: int, error: str, *, requeue: bool = False) -> None:
    status = "pending" if requeue else "failed"
    conn.execute(
        """
        UPDATE chunks
        SET embed_status = %s,
            embed_locked_at = NULL,
            embed_error = %s
        WHERE id = %s
        """,
        (status, error[:2000], chunk_id),
    )


def _assert_dims_compatible(settings: Settings) -> None:
    expected = active_dims(settings)
    with connect(settings) as conn:
        apply_embed_queue_migration(conn)
        col_dims = get_vector_column_dims(conn)
        if col_dims is not None and col_dims != expected:
            provider = (settings.embedding_provider or "").strip().lower()
            tip = (
                "python -m ingestion.cli prepare-local-index (then EMBEDDING_PROVIDER=local)"
                if provider == "local"
                else "python -m ingestion.cli prepare-gemini-index (then EMBEDDING_PROVIDER=gemini)"
            )
            raise RuntimeError(
                f"DB column is vector({col_dims}) but active provider expects {expected}-d. "
                f"Run: {tip}. Do not mix models in one index."
            )
        wrong_dim = conn.execute(
            """
            SELECT COUNT(*) FROM chunks
            WHERE embedding IS NOT NULL AND vector_dims(embedding) <> %s
            """,
            (expected,),
        ).fetchone()[0]
        if int(wrong_dim) > 0:
            raise RuntimeError(
                f"Found {wrong_dim} embeddings with wrong dimensions. "
                f"Run prepare-local-index or prepare-gemini-index first."
            )


def run_embed_worker(
    settings: Settings | None = None,
    *,
    batch_size: int | None = None,
    max_per_run: int | None = 500,
    max_attempts: int = 5,
    pause_on_quota_seconds: int = 0,
) -> WorkerResult:
    """
    Process pending chunk embeddings. Safe to re-run.
    On quota/rate exhaustion (Gemini): requeues and stops (crawl unaffected).
    Local provider embeds claimed rows in micro-batches.
    """
    settings = settings or get_settings()
    result = WorkerResult(
        provider=(settings.embedding_provider or "gemini").lower(),
        model_id=active_model_id(settings),
    )
    bs = batch_size if batch_size is not None else int(settings.embed_batch_size or 8)

    _assert_dims_compatible(settings)
    embedder = get_embedder(settings)
    console.print(
        f"[cyan]Embed worker[/] provider={result.provider} "
        f"model={result.model_id} dims={embedder.dims} batch_size={bs}"
    )

    with connect(settings) as conn:
        apply_embed_queue_migration(conn)
        recovered = _recover_stale_processing(conn)
        if recovered:
            console.print(f"[yellow]Recovered[/] {recovered} stale processing locks → pending")

    processed = 0
    while True:
        if max_per_run is not None and processed >= max_per_run:
            result.stopped_reason = "max_per_run"
            break

        take = bs
        if max_per_run is not None:
            take = min(bs, max_per_run - processed)
        if take <= 0:
            break

        with connect(settings) as conn:
            claimed = claim_chunks(conn, batch_size=take, max_attempts=max_attempts)
            conn.commit()

        if not claimed:
            result.stopped_reason = "queue_empty"
            console.print("[green]Embed queue empty[/] (or all remaining exceeded max attempts)")
            break

        result.claimed += len(claimed)
        console.print(f"[cyan]Claimed[/] {len(claimed)} chunks (total this run: {result.claimed})")

        ids = [cid for cid, _ in claimed]
        texts = [text for _, text in claimed]

        try:
            vectors = embedder.embed_batch(
                texts,
                task_type="RETRIEVAL_DOCUMENT",
                batch_size=bs,
            )
            if len(vectors) != len(ids):
                raise RuntimeError("embed_batch returned unexpected length")
            for vec in vectors:
                if len(vec) != embedder.dims:
                    raise RuntimeError(
                        f"Bad embedding length {len(vec)}; expected {embedder.dims}"
                    )

            with connect(settings) as conn:
                for chunk_id, vec in zip(ids, vectors, strict=True):
                    mark_completed(conn, chunk_id, vec, model_id=embedder.model_id)
            result.completed += len(ids)
            processed += len(ids)
            console.print(
                f"  progress completed={result.completed} failed={result.failed}"
            )
        except Exception as exc:  # noqa: BLE001
            if is_quota_exhausted(exc) or is_rate_limit_error(exc):
                console.print(
                    f"[yellow]Quota/rate limited[/] — deferring remaining work. ({exc})"
                )
                with connect(settings) as conn:
                    conn.execute(
                        """
                        UPDATE chunks
                        SET embed_status = 'pending',
                            embed_locked_at = NULL,
                            embed_error = %s,
                            embed_attempts = GREATEST(embed_attempts - 1, 0)
                        WHERE id = ANY(%s) AND embed_status = 'processing'
                        """,
                        (f"deferred: {exc}"[:2000], ids),
                    )
                result.deferred += len(ids)
                result.stopped_reason = "quota_exhausted"
                if pause_on_quota_seconds > 0:
                    console.print(f"Sleeping {pause_on_quota_seconds}s before exit...")
                    time.sleep(pause_on_quota_seconds)
                _print_summary(result)
                return result

            # Hard failure for the whole claimed batch → mark failed individually
            with connect(settings) as conn:
                for chunk_id in ids:
                    mark_failed(conn, chunk_id, str(exc), requeue=False)
            result.failed += len(ids)
            processed += len(ids)
            console.print(f"[red]Failed batch[/] size={len(ids)}: {exc}")

    _print_summary(result)
    return result


def _print_summary(result: WorkerResult) -> None:
    console.print(
        f"[bold green]Embed worker done[/] "
        f"provider={result.provider} model={result.model_id} "
        f"claimed={result.claimed} completed={result.completed} "
        f"failed={result.failed} deferred={result.deferred} "
        f"reason={result.stopped_reason}"
    )


def print_status(settings: Settings | None = None) -> dict[str, Any]:
    from ingestion.db import get_pipeline_status

    settings = settings or get_settings()
    stats = get_pipeline_status(settings)
    stats["embedding_provider"] = (settings.embedding_provider or "gemini").lower()
    stats["active_model"] = active_model_id(settings)
    stats["active_dims"] = active_dims(settings)
    try:
        with connect(settings) as conn:
            apply_embed_queue_migration(conn)
            stats["db_vector_dims"] = get_vector_column_dims(conn)
            by_model = dict(
                conn.execute(
                    """
                    SELECT coalesce(embed_model, '(null)'), COUNT(*)
                    FROM chunks WHERE embedding IS NOT NULL
                    GROUP BY 1
                    """
                ).fetchall()
            )
            stats["embedded_by_model"] = by_model
    except Exception as exc:  # noqa: BLE001
        stats["db_probe_error"] = str(exc)

    console.print("[bold]Pipeline status[/]")
    for k, v in stats.items():
        console.print(f"  {k:28s} {v}")
    return stats
