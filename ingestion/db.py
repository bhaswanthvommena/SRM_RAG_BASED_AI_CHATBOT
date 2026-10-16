from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Any, Iterator

import orjson
import psycopg
from pgvector.psycopg import register_vector
from rich.console import Console

from ingestion.chunking import chunk_document
from ingestion.config import Settings, get_settings
from ingestion.models import ParsedDocument

console = Console()


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    settings = settings or get_settings()
    conn = psycopg.connect(settings.database_url)
    register_vector(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_embed_queue_migration(conn: psycopg.Connection) -> None:
    """Idempotent schema patch so existing DBs get the embed queue columns."""
    conn.execute(
        """
        ALTER TABLE chunks
            ADD COLUMN IF NOT EXISTS content_hash TEXT,
            ADD COLUMN IF NOT EXISTS embed_status TEXT NOT NULL DEFAULT 'pending',
            ADD COLUMN IF NOT EXISTS embed_attempts INT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS embed_error TEXT,
            ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS embed_locked_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS embed_model TEXT
        """
    )
    conn.execute(
        """
        UPDATE chunks
        SET embed_status = 'completed',
            embedded_at = COALESCE(embedded_at, NOW())
        WHERE embedding IS NOT NULL
          AND (embed_status IS NULL OR embed_status = 'pending')
        """
    )
    # Infer model for legacy 768-d vectors (Gemini) when unset
    conn.execute(
        """
        UPDATE chunks
        SET embed_model = 'gemini-embedding-001'
        WHERE embedding IS NOT NULL
          AND embed_model IS NULL
          AND vector_dims(embedding) = 768
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_embed_pending
            ON chunks (embed_status, id)
            WHERE embed_status IN ('pending', 'failed')
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_content_hash
            ON chunks (document_id, content_hash)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunks_embed_model
            ON chunks (embed_model)
            WHERE embedding IS NOT NULL
        """
    )


def get_vector_column_dims(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        WHERE c.relname = 'chunks' AND a.attname = 'embedding' AND NOT a.attisdropped
        """
    ).fetchone()
    if not row or not row[0]:
        return None
    typ = row[0]  # e.g. vector(768)
    if typ.startswith("vector(") and typ.endswith(")"):
        try:
            return int(typ[7:-1])
        except ValueError:
            return None
    return None


def prepare_vector_index(
    settings: Settings | None = None,
    *,
    target_dims: int,
    target_model: str,
    label: str,
) -> dict[str, int]:
    """
    Safely switch the vector column to a new embedding model/dims.

    Does NOT delete documents or chunk text. Clears incompatible vectors,
    requeues them as pending, alters column dims, recreates HNSW index.
    """
    settings = settings or get_settings()
    stats = {
        "cleared_vectors": 0,
        "requeued": 0,
        "column_dims_before": 0,
        "column_dims_after": 0,
    }
    reason = f"requeued: switching to {label} (incompatible prior vector)"

    with connect(settings) as conn:
        apply_embed_queue_migration(conn)
        before = get_vector_column_dims(conn) or 0
        stats["column_dims_before"] = before

        cleared = conn.execute(
            """
            UPDATE chunks
            SET embedding = NULL,
                embed_status = 'pending',
                embed_model = NULL,
                embedded_at = NULL,
                embed_locked_at = NULL,
                embed_error = %s,
                embed_attempts = 0
            WHERE embedding IS NOT NULL
              AND (
                    vector_dims(embedding) <> %s
                 OR coalesce(embed_model, '') <> %s
              )
            """,
            (reason, target_dims, target_model),
        )
        stats["cleared_vectors"] = cleared.rowcount if cleared is not None else 0

        requeued = conn.execute(
            """
            UPDATE chunks
            SET embed_status = 'pending',
                embed_locked_at = NULL,
                embed_attempts = 0
            WHERE embed_status IN ('failed', 'processing')
               OR (embed_status = 'completed' AND embedding IS NULL)
            """
        )
        stats["requeued"] = requeued.rowcount if requeued is not None else 0

        if before != target_dims:
            console.print(
                f"[yellow]Altering[/] chunks.embedding vector({before}) -> vector({target_dims})"
            )
            conn.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")
            leftover = conn.execute(
                """
                SELECT COUNT(*) FROM chunks
                WHERE embedding IS NOT NULL AND vector_dims(embedding) <> %s
                """,
                (target_dims,),
            ).fetchone()[0]
            if int(leftover) > 0:
                raise RuntimeError(
                    f"Cannot alter vector column: {leftover} rows still have wrong dims"
                )
            conn.execute(
                f"""
                ALTER TABLE chunks
                ALTER COLUMN embedding TYPE vector({target_dims})
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
                    ON chunks USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """
            )

        after = get_vector_column_dims(conn) or 0
        stats["column_dims_after"] = after
        console.print(
            f"[bold green]prepare-index done[/] label={label} "
            f"cleared={stats['cleared_vectors']} column={after}-d target_model={target_model}"
        )
    return stats


def prepare_local_index(
    settings: Settings | None = None,
    *,
    target_dims: int = 384,
    target_model: str = "BAAI/bge-small-en-v1.5",
) -> dict[str, int]:
    """Switch vector column to local BGE (384-d)."""
    return prepare_vector_index(
        settings,
        target_dims=target_dims,
        target_model=target_model,
        label="local BGE 384-d",
    )


def prepare_gemini_index(
    settings: Settings | None = None,
    *,
    target_dims: int | None = None,
    target_model: str | None = None,
) -> dict[str, int]:
    """Switch vector column to hosted Gemini embeddings (default 768-d)."""
    settings = settings or get_settings()
    from ingestion.embeddings import GEMINI_EMBED_MODEL_ID

    dims = int(target_dims if target_dims is not None else settings.embedding_dims or 768)
    model = target_model or settings.gemini_embedding_model or GEMINI_EMBED_MODEL_ID
    return prepare_vector_index(
        settings,
        target_dims=dims,
        target_model=model,
        label=f"Gemini {dims}-d",
    )


def chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def upsert_document(conn: psycopg.Connection, doc: ParsedDocument) -> int:
    row = conn.execute(
        """
        INSERT INTO documents (
            url, canonical_url, wp_type, wp_id, title, school, department,
            doc_type, published_at, modified_at, content_hash, source, is_stale, metadata
        ) VALUES (
            %(url)s, %(url)s, %(wp_type)s, %(wp_id)s, %(title)s, %(school)s, %(department)s,
            %(doc_type)s, %(published_at)s, %(modified_at)s, %(content_hash)s, %(source)s,
            %(is_stale)s, %(metadata)s::jsonb
        )
        ON CONFLICT (url) DO UPDATE SET
            wp_type = EXCLUDED.wp_type,
            wp_id = EXCLUDED.wp_id,
            title = EXCLUDED.title,
            school = EXCLUDED.school,
            department = EXCLUDED.department,
            doc_type = EXCLUDED.doc_type,
            published_at = EXCLUDED.published_at,
            modified_at = EXCLUDED.modified_at,
            content_hash = EXCLUDED.content_hash,
            source = EXCLUDED.source,
            is_stale = EXCLUDED.is_stale,
            metadata = EXCLUDED.metadata,
            crawled_at = NOW()
        RETURNING id
        """,
        {
            "url": doc.url,
            "wp_type": doc.wp_type or "page",
            "wp_id": doc.wp_id,
            "title": doc.title,
            "school": doc.school,
            "department": doc.department,
            "doc_type": doc.doc_type,
            "published_at": doc.published_at,
            "modified_at": doc.modified_at,
            "content_hash": doc.content_hash,
            "source": doc.source,
            "is_stale": doc.is_stale,
            "metadata": orjson.dumps(doc.structured).decode(),
        },
    ).fetchone()
    assert row is not None
    return int(row[0])


def document_hash_unchanged(conn: psycopg.Connection, url: str, content_hash: str) -> tuple[int | None, bool]:
    row = conn.execute(
        "SELECT id, content_hash FROM documents WHERE url = %s",
        (url,),
    ).fetchone()
    if not row:
        return None, False
    return int(row[0]), row[1] == content_hash


def upsert_text_chunks(
    conn: psycopg.Connection,
    document_id: int,
    chunks: list[Any],
) -> tuple[int, int]:
    """
    Upsert chunk text without touching embeddings for unchanged hashes.
    Returns (inserted_or_reset, kept_completed).
    """
    # Remove ords that no longer exist
    new_ords = [c.ord for c in chunks]
    if new_ords:
        conn.execute(
            "DELETE FROM chunks WHERE document_id = %s AND NOT (ord = ANY(%s))",
            (document_id, new_ords),
        )
    else:
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
        return 0, 0

    reset = 0
    kept = 0
    for c in chunks:
        chash = chunk_text_hash(c.text)
        existing = conn.execute(
            """
            SELECT id, content_hash, embed_status, embedding IS NOT NULL
            FROM chunks WHERE document_id = %s AND ord = %s
            """,
            (document_id, c.ord),
        ).fetchone()

        if (
            existing
            and existing[1] == chash
            and existing[2] == "completed"
            and existing[3]
        ):
            # Unchanged + already embedded — keep
            conn.execute(
                """
                UPDATE chunks SET
                    heading_path = %s,
                    token_count = %s,
                    metadata = %s::jsonb
                WHERE id = %s
                """,
                (c.heading_path, c.token_count, orjson.dumps(c.metadata).decode(), existing[0]),
            )
            kept += 1
            continue

        if existing:
            conn.execute(
                """
                UPDATE chunks SET
                    heading_path = %s,
                    text = %s,
                    token_count = %s,
                    content_hash = %s,
                    metadata = %s::jsonb,
                    embedding = NULL,
                    embed_status = 'pending',
                    embed_error = NULL,
                    embedded_at = NULL,
                    embed_locked_at = NULL
                WHERE id = %s
                """,
                (
                    c.heading_path,
                    c.text,
                    c.token_count,
                    chash,
                    orjson.dumps(c.metadata).decode(),
                    existing[0],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO chunks (
                    document_id, ord, heading_path, text, token_count,
                    content_hash, embedding, embed_status, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, NULL, 'pending', %s::jsonb)
                """,
                (
                    document_id,
                    c.ord,
                    c.heading_path,
                    c.text,
                    c.token_count,
                    chash,
                    orjson.dumps(c.metadata).decode(),
                ),
            )
        reset += 1
    return reset, kept


def upsert_faculty(conn: psycopg.Connection, document_id: int, doc: ParsedDocument) -> None:
    s = doc.structured or {}
    name = s.get("name") or doc.title
    if not name:
        return
    conn.execute("DELETE FROM faculty WHERE document_id = %s", (document_id,))
    conn.execute(
        """
        INSERT INTO faculty (document_id, name, designation, department, school, email, research_areas, url, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            document_id,
            name,
            s.get("designation"),
            s.get("department") or doc.department,
            doc.school,
            s.get("email"),
            s.get("research_areas") or [],
            doc.url,
            orjson.dumps(s).decode(),
        ),
    )


def upsert_programme(conn: psycopg.Connection, document_id: int, doc: ParsedDocument) -> None:
    s = doc.structured or {}
    name = s.get("name") or doc.title
    if not name:
        return
    conn.execute("DELETE FROM programmes WHERE document_id = %s", (document_id,))
    conn.execute(
        """
        INSERT INTO programmes (
            document_id, name, school, department, level, eligibility, duration, fee_summary, url, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            document_id,
            name,
            doc.school,
            doc.department,
            s.get("level"),
            s.get("eligibility"),
            s.get("duration"),
            s.get("fee_summary"),
            doc.url,
            orjson.dumps(s).decode(),
        ),
    )


def load_parsed_docs(settings: Settings) -> list[ParsedDocument]:
    docs: list[ParsedDocument] = []
    for path in sorted(settings.parsed_dir.glob("*.json")):
        docs.append(ParsedDocument.model_validate(orjson.loads(path.read_bytes())))
    return docs


def filter_docs(
    docs: list[ParsedDocument],
    *,
    limit: int | None = None,
    doc_types: list[str] | None = None,
    url_contains: list[str] | None = None,
) -> list[ParsedDocument]:
    docs = [d for d in docs if len(d.text) >= 40]
    if doc_types:
        allowed = set(doc_types)
        docs = [d for d in docs if d.doc_type in allowed]
    if url_contains:
        docs = [
            d
            for d in docs
            if any(needle.lower() in d.url.lower() for needle in url_contains)
        ]
    if limit is not None:
        docs = docs[:limit]
    return docs


def run_persist(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    doc_types: list[str] | None = None,
    url_contains: list[str] | None = None,
) -> dict[str, int]:
    """
    Persist documents + text chunks to Postgres WITHOUT calling any embedding API.
    Unchanged chunk hashes that are already completed are left alone.
    """
    settings = settings or get_settings()
    docs = filter_docs(
        load_parsed_docs(settings),
        limit=limit,
        doc_types=doc_types,
        url_contains=url_contains,
    )

    persisted = 0
    skipped_unchanged = 0
    chunks_pending = 0
    chunks_kept = 0

    with connect(settings) as conn:
        apply_embed_queue_migration(conn)
        for doc in docs:
            existing_id, hash_same = document_hash_unchanged(conn, doc.url, doc.content_hash)
            if existing_id is not None and hash_same:
                # Still ensure chunks exist / statuses are correct
                n_chunks = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = %s",
                    (existing_id,),
                ).fetchone()[0]
                if int(n_chunks) > 0:
                    skipped_unchanged += 1
                    continue

            doc_id = upsert_document(conn, doc)
            chunks = chunk_document(doc)
            if not chunks:
                continue
            reset, kept = upsert_text_chunks(conn, doc_id, chunks)
            chunks_pending += reset
            chunks_kept += kept
            if doc.doc_type == "faculty":
                upsert_faculty(conn, doc_id, doc)
            elif doc.doc_type == "programme":
                upsert_programme(conn, doc_id, doc)
            persisted += 1
            if persisted % 25 == 0:
                console.print(
                    f"  persisted {persisted}/{len(docs)} "
                    f"(pending chunks +{chunks_pending}, kept {chunks_kept})"
                )
                conn.commit()

    stats = {
        "documents_persisted": persisted,
        "documents_skipped_unchanged": skipped_unchanged,
        "chunks_queued_or_reset": chunks_pending,
        "chunks_kept_completed": chunks_kept,
    }
    console.print(
        f"[bold green]Persist done[/] docs={persisted} "
        f"skipped_unchanged={skipped_unchanged} "
        f"chunks_queued={chunks_pending} chunks_kept={chunks_kept}"
    )
    return stats


def get_pipeline_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    parsed_files = len(list(settings.parsed_dir.glob("*.json"))) if settings.parsed_dir.exists() else 0
    raw_files = len(list(settings.raw_dir.glob("*.html.gz"))) if settings.raw_dir.exists() else 0
    pdf_files = len(list(settings.pdf_dir.glob("*"))) if settings.pdf_dir.exists() else 0
    manifest_count = 0
    if settings.manifest_path.exists():
        with settings.manifest_path.open("rb") as f:
            manifest_count = sum(1 for line in f if line.strip())

    out: dict[str, Any] = {
        "discovered_manifest": manifest_count,
        "fetched_raw_html": raw_files,
        "parsed_json": parsed_files,
        "pdf_files": pdf_files,
    }

    try:
        with connect(settings) as conn:
            apply_embed_queue_migration(conn)
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            by_status = dict(
                conn.execute(
                    "SELECT embed_status, COUNT(*) FROM chunks GROUP BY embed_status"
                ).fetchall()
            )
            out.update(
                {
                    "documents": int(docs),
                    "chunks": int(chunks),
                    "embed_pending": int(by_status.get("pending", 0)),
                    "embed_processing": int(by_status.get("processing", 0)),
                    "embed_completed": int(by_status.get("completed", 0)),
                    "embed_failed": int(by_status.get("failed", 0)),
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["db_error"] = str(exc)

    return out


# Back-compat alias: load now means persist only (no Gemini)
def run_load(
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    skip_embed: bool = True,  # ignored; embedding is a separate worker
    doc_types: list[str] | None = None,
    url_contains: list[str] | None = None,
) -> int:
    if not skip_embed:
        console.print(
            "[yellow]Note:[/] embedding is decoupled. "
            "Use `python -m ingestion.cli persist` then `embed-worker`."
        )
    stats = run_persist(
        settings,
        limit=limit,
        doc_types=doc_types,
        url_contains=url_contains,
    )
    return stats["documents_persisted"]
