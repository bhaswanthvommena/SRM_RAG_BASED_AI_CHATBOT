"""Structured logs for Cloud Logging.

High-cardinality values (request id, IP hash, source URLs, chunk ids) stay in
the log body. `labels` is limited to low-cardinality fields that are safe to
promote to metric labels later.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from contextvars import ContextVar
from typing import Any

ACCESS_LOGGER = logging.getLogger("app.access")

_PROCESS_SALT = secrets.token_hex(16)
_timings: ContextVar[dict[str, float] | None] = ContextVar("request_timings", default=None)

LABEL_KEYS = ("route", "status_class", "error_category", "intent")
_BANNED = {
    "question",
    "message",
    "prompt",
    "query",
    "rewritten_query",
    "history",
    "contents",
    "database_url",
    "api_key",
}


def ip_salt(configured: str) -> str:
    return configured.strip() or _PROCESS_SALT


def hash_ip(ip: str, salt: str) -> str:
    material = f"{ip_salt(salt)}|{ip}".encode()
    return hashlib.sha256(material).hexdigest()[:16]


def status_class(status: int) -> str:
    bucket = max(1, min(5, status // 100))
    return f"{bucket}xx"


def begin_timings() -> dict[str, float]:
    bucket: dict[str, float] = {}
    _timings.set(bucket)
    return bucket


def bind_timings(bucket: dict[str, float] | None) -> None:
    _timings.set(bucket if bucket is not None else {})


def record_timing(name: str, milliseconds: float) -> None:
    bucket = _timings.get()
    if bucket is None:
        return
    bucket[name] = round(milliseconds, 1)


def current_timings() -> dict[str, float]:
    return dict(_timings.get() or {})


def clear_timings() -> None:
    _timings.set(None)


def log_event(event: str, *, labels: dict[str, Any], **fields: Any) -> None:
    safe_labels = {key: labels[key] for key in LABEL_KEYS if key in labels and labels[key] is not None}
    payload: dict[str, Any] = {"event": event, "labels": safe_labels}
    for key, value in fields.items():
        if key in _BANNED or key in LABEL_KEYS:
            continue
        payload[key] = value
    ACCESS_LOGGER.info(json.dumps(payload, default=str, separators=(",", ":")))
