from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.client_ip import client_ip
from app.config import get_settings
from app.db import get_conn
from app.generation import BUSY_MESSAGE, ModelError, generate_answer, is_greeting
from app.observe import (
    begin_timings,
    bind_timings,
    clear_timings,
    current_timings,
    hash_ip,
    log_event,
    record_timing,
    status_class,
)
from app.ratelimit import RateLimiter
from app.retrieval import RetrievedChunk, build_context, hybrid_search, rewrite_query
from app.schemas import ChatRequest, ChatResponse, Citation, FeedbackRequest, HealthResponse

logger = logging.getLogger(__name__)
settings = get_settings()
limiter = RateLimiter(
    per_minute=settings.rate_limit_per_minute,
    per_hour=settings.rate_limit_per_hour,
    max_keys=settings.rate_limit_max_keys,
)
_health_lock = threading.Lock()
_health_cache: dict[str, Any] = {"mono": 0.0, "documents": 0, "chunks": 0}


def reset_health_cache() -> None:
    with _health_lock:
        _health_cache["mono"] = 0.0
        _health_cache["documents"] = 0
        _health_cache["chunks"] = 0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Warm local embedder (BGE) so the first chat request is not a cold model load.
    provider = (settings.embedding_provider or "").strip().lower()
    if provider == "local":
        try:
            from app.embeddings import get_embedder

            get_embedder()
            logger.info("Local embedder warmed up")
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedder_warmup_failed error_type=%s", type(exc).__name__)
    yield


app = FastAPI(
    title="SRM AP RAG Chatbot",
    version="0.1.0",
    description="RAG assistant over srmap.edu.in",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def assign_request_id(request: Request, call_next):
    request_id = uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {"detail": exc.detail}
    if request_id:
        body["request_id"] = request_id
    headers = {"X-Request-Id": request_id} if request_id else None
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, _exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {"detail": "Request is invalid."}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=422, content=body)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "srmap-rag-api",
        "docs": "/docs",
        "health": "/health",
        "chat_stream": "/chat/stream",
    }


def _cache_fresh() -> bool:
    with _health_lock:
        age = time.monotonic() - float(_health_cache["mono"])
        return _health_cache["mono"] > 0 and age < settings.health_cache_seconds


def _store_counts(documents: int, chunks: int) -> None:
    with _health_lock:
        _health_cache["mono"] = time.monotonic()
        _health_cache["documents"] = documents
        _health_cache["chunks"] = chunks


def _cached_counts() -> tuple[int, int]:
    with _health_lock:
        return int(_health_cache["documents"]), int(_health_cache["chunks"])


def _refresh_counts() -> tuple[int, int]:
    with get_conn() as conn:
        documents = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    _store_counts(documents, chunks)
    return documents, chunks


def _summary_counts() -> tuple[int, int, str]:
    """Return cached index counts, refreshing at most once per health_cache_seconds."""
    if _cache_fresh():
        documents, chunks = _cached_counts()
        return documents, chunks, "ok"
    try:
        documents, chunks = _refresh_counts()
        return documents, chunks, "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_count_failed error_type=%s", type(exc).__name__)
        documents, chunks = _cached_counts()
        return documents, chunks, "degraded"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    documents, chunks, db_status = _summary_counts()
    details: dict[str, Any] = {"probe": "health"}
    if db_status != "ok":
        details["error_category"] = "database"
    return HealthResponse(status=db_status, documents=documents, chunks=chunks, details=details)


@app.get("/health/live", response_model=HealthResponse)
def health_live() -> HealthResponse:
    """Process liveness. Does not query the database."""
    documents, chunks = _cached_counts()
    return HealthResponse(
        status="ok",
        documents=documents,
        chunks=chunks,
        details={"probe": "live"},
    )


@app.get("/health/ready", response_model=HealthResponse)
def health_ready() -> JSONResponse:
    """Dependency readiness. Uses SELECT 1, not a full table count, on every probe."""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        if _cache_fresh():
            documents, chunks = _cached_counts()
        else:
            documents, chunks = _refresh_counts()
        body = HealthResponse(
            status="ok",
            documents=documents,
            chunks=chunks,
            details={"probe": "ready"},
        )
        return JSONResponse(status_code=200, content=body.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_ready_failed error_type=%s", type(exc).__name__)
        documents, chunks = _cached_counts()
        body = HealthResponse(
            status="degraded",
            documents=documents,
            chunks=chunks,
            details={"probe": "ready", "error_category": "database"},
        )
        return JSONResponse(status_code=503, content=body.model_dump())


@app.get("/suggestions")
def suggestions() -> dict[str, list[str]]:
    return {
        "suggestions": [
            "What is the B.Tech tuition fee and available scholarships?",
            "How do I apply for undergraduate admission via SRMJEEE?",
            "List Computer Science faculty and their research areas",
            "What hostels are available on campus?",
            "What were the recent placement statistics?",
            "What programmes does Paari School of Business offer?",
        ]
    }


def _history_dicts(req: ChatRequest) -> list[dict[str, str]]:
    cap = max(0, settings.max_history_messages)
    items = req.history[-cap:] if cap else []
    return [{"role": m.role, "content": m.content} for m in items]


def _request_ip(request: Request) -> str:
    return client_ip(request, settings.trusted_proxy_hops)


def _enforce_rate_limit(request: Request) -> None:
    ip = _request_ip(request)
    request.state.ip_hash = hash_ip(ip, settings.log_ip_salt)
    if limiter.allow(ip):
        return
    raise HTTPException(status_code=429, detail="Too many requests. Please wait and try again.")


def _retrieve(message: str, history: list[dict[str, str]]) -> tuple[str, str, list[RetrievedChunk], list[dict[str, Any]]]:
    if is_greeting(message):
        return message, "chitchat", [], []
    started = time.perf_counter()
    try:
        rewritten, intent = rewrite_query(message, history, settings)
        chunks = hybrid_search(rewritten, intent=intent, settings=settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Retrieval is temporarily unavailable.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("retrieval_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Retrieval failed.") from exc
    record_timing("retrieval_ms", (time.perf_counter() - started) * 1000)
    _, citations = build_context(chunks)
    return rewritten, intent, chunks, citations


def _persist_exchange(
    *,
    request_id: str,
    route: str,
    conversation_id: str,
    user_message: str,
    answer: str,
    chunks: list[RetrievedChunk],
    citations: list[dict[str, Any]],
) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id) VALUES (%s::uuid)
                ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                """,
                (conversation_id,),
            )
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content)
                VALUES (%s::uuid, 'user', %s)
                """,
                (conversation_id, user_message),
            )
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, retrieved_chunk_ids, citations)
                VALUES (%s::uuid, 'assistant', %s, %s, %s::jsonb)
                """,
                (
                    conversation_id,
                    answer,
                    [c.chunk_id for c in chunks],
                    json.dumps(citations),
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        log_event(
            "chat_persist",
            labels={
                "route": route,
                "status_class": "5xx",
                "error_category": "persistence",
                "intent": "unknown",
            },
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        return False


def _log_chat(
    *,
    request: Request,
    route: str,
    status: int,
    error_category: str,
    intent: str,
    citation_count: int,
    chunk_ids: list[int],
    source_urls: list[str],
    started: float,
) -> None:
    timings = current_timings()
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    log_event(
        "chat_request",
        labels={
            "route": route,
            "status_class": status_class(status),
            "error_category": error_category,
            "intent": intent or "unknown",
        },
        request_id=request.state.request_id,
        ip_hash=getattr(request.state, "ip_hash", None),
        status=status,
        citation_count=citation_count,
        chunk_ids=chunk_ids,
        source_urls=source_urls,
        timings_ms=timings,
    )


def _chat_outcome(
    request: Request,
    req: ChatRequest,
) -> tuple[str, str, list[RetrievedChunk], list[dict[str, Any]], list[dict[str, str]]]:
    _enforce_rate_limit(request)
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Request is invalid.")
    history = _history_dicts(req)
    request.state.timings = begin_timings()
    rewritten, intent, chunks, citations = _retrieve(req.message, history)
    return rewritten, intent, chunks, citations, history


@app.post("/chat", response_model=ChatResponse)
def chat(request: Request, req: ChatRequest) -> ChatResponse:
    if req.stream:
        raise HTTPException(status_code=400, detail="Use /chat/stream for streaming")

    started = time.perf_counter()
    status = 200
    error_category = "none"
    intent = "unknown"
    chunks: list[RetrievedChunk] = []
    citations: list[dict[str, Any]] = []
    try:
        rewritten, intent, chunks, citations, history = _chat_outcome(request, req)
        try:
            answer, citations = generate_answer(
                req.message, chunks, history=history, settings=settings, stream=False
            )
        except ModelError as exc:
            status = 503
            error_category = exc.category
            raise HTTPException(status_code=503, detail=exc.public_message) from exc
        assert isinstance(answer, str)
        if is_greeting(req.message):
            citations = []
        conv_id = req.conversation_id or str(uuid.uuid4())
        _persist_exchange(
            request_id=request.state.request_id,
            route="/chat",
            conversation_id=conv_id,
            user_message=req.message,
            answer=answer,
            chunks=chunks,
            citations=citations,
        )
        return ChatResponse(
            conversation_id=conv_id,
            answer=answer,
            citations=[Citation(**c) for c in citations],
            intent=intent,
            rewritten_query=rewritten,
        )
    except HTTPException as exc:
        status = exc.status_code
        if error_category == "none":
            error_category = {
                429: "rate_limit",
                422: "validation",
                503: "retrieval",
            }.get(exc.status_code, "retrieval" if exc.status_code >= 500 else "validation")
        raise
    finally:
        _log_chat(
            request=request,
            route="/chat",
            status=status,
            error_category=error_category,
            intent=intent,
            citation_count=len(citations),
            chunk_ids=[c.chunk_id for c in chunks],
            source_urls=[c.url for c in chunks if c.url],
            started=started,
        )
        clear_timings()


async def chat_event_stream(
    request: Request,
    *,
    request_id: str,
    conversation_id: str,
    message: str,
    history: list[dict[str, str]],
    intent: str,
    rewritten: str,
    chunks: list[RetrievedChunk],
    citations: list[dict[str, Any]],
    started: float,
):
    """SSE events. Failures before the first yield are HTTP errors in the caller.

    After meta is sent, model failures become an `error` event and the stream ends.
    """
    error_category = "none"
    status = 200
    gemini_started = time.perf_counter()
    bind_timings(getattr(request.state, "timings", None))
    try:
        yield {
            "event": "meta",
            "data": json.dumps(
                {
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "intent": intent,
                    "rewritten_query": rewritten,
                    "citations": citations,
                }
            ),
        }
        stream = generate_answer(message, chunks, history=history, settings=settings, stream=True)
        full: list[str] = []
        try:
            for piece in stream:  # type: ignore[union-attr]
                if await request.is_disconnected():
                    error_category = "client_disconnect"
                    return
                if "ttft_ms" not in current_timings():
                    record_timing("ttft_ms", (time.perf_counter() - gemini_started) * 1000)
                full.append(piece)
                yield {"event": "token", "data": json.dumps({"text": piece})}
        except ModelError as exc:
            error_category = exc.category
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "request_id": request_id,
                        "error": exc.category,
                        "message": exc.public_message,
                    }
                ),
            }
            return
        except Exception as exc:  # noqa: BLE001
            error_category = "generation_failed"
            logger.warning("stream_failed error_type=%s", type(exc).__name__)
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "request_id": request_id,
                        "error": "generation_failed",
                        "message": BUSY_MESSAGE,
                    }
                ),
            }
            return

        if await request.is_disconnected():
            error_category = "client_disconnect"
            return

        _persist_exchange(
            request_id=request_id,
            route="/chat/stream",
            conversation_id=conversation_id,
            user_message=message,
            answer="".join(full),
            chunks=chunks,
            citations=citations,
        )
        yield {"event": "done", "data": json.dumps({"conversation_id": conversation_id, "request_id": request_id})}
    except asyncio.CancelledError:
        error_category = "client_disconnect"
        raise
    except Exception as exc:  # noqa: BLE001
        if error_category == "none":
            error_category = "client_disconnect" if await request.is_disconnected() else "generation_failed"
            if error_category != "client_disconnect":
                logger.warning("stream_closed error_type=%s", type(exc).__name__)
        return
    finally:
        record_timing("gemini_ms", (time.perf_counter() - gemini_started) * 1000)
        _log_chat(
            request=request,
            route="/chat/stream",
            status=status,
            error_category=error_category,
            intent=intent,
            citation_count=len(citations),
            chunk_ids=[c.chunk_id for c in chunks],
            source_urls=[c.url for c in chunks if c.url],
            started=started,
        )
        clear_timings()


@app.post("/chat/stream")
async def chat_stream(request: Request, req: ChatRequest) -> EventSourceResponse:
    started = time.perf_counter()
    try:
        rewritten, intent, chunks, citations, history = _chat_outcome(request, req)
    except HTTPException as exc:
        category = {
            429: "rate_limit",
            422: "validation",
            503: "retrieval",
        }.get(exc.status_code, "retrieval" if exc.status_code >= 500 else "validation")
        _log_chat(
            request=request,
            route="/chat/stream",
            status=exc.status_code,
            error_category=category,
            intent="unknown",
            citation_count=0,
            chunk_ids=[],
            source_urls=[],
            started=started,
        )
        clear_timings()
        raise

    conv_id = req.conversation_id or str(uuid.uuid4())
    return EventSourceResponse(
        chat_event_stream(
            request,
            request_id=request.state.request_id,
            conversation_id=conv_id,
            message=req.message,
            history=history,
            intent=intent,
            rewritten=rewritten,
            chunks=chunks,
            citations=citations,
            started=started,
        )
    )


@app.post("/feedback")
def feedback(req: FeedbackRequest) -> dict[str, str]:
    try:
        with get_conn() as conn:
            if req.message_id:
                conn.execute(
                    "INSERT INTO feedback (message_id, rating, comment) VALUES (%s, %s, %s)",
                    (req.message_id, req.rating, req.comment),
                )
            elif req.conversation_id:
                row = conn.execute(
                    """
                    SELECT id FROM messages
                    WHERE conversation_id = %s::uuid AND role = 'assistant'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (req.conversation_id,),
                ).fetchone()
                if row:
                    conn.execute(
                        "INSERT INTO feedback (message_id, rating, comment) VALUES (%s, %s, %s)",
                        (row[0], req.rating, req.comment),
                    )
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Feedback could not be saved.") from exc
