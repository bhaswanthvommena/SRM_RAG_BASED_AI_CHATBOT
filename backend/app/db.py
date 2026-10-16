from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector

from app.config import Settings, get_settings


@contextmanager
def get_conn(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
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
