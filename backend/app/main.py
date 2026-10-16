from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.db import get_conn
from app.generation import generate_answer, is_greeting
from app.retrieval import build_context, hybrid_search, rewrite_query
from app.schemas import ChatRequest, ChatResponse, Citation, FeedbackRequest, HealthResponse

logger = logging.getLogger(__name__)
settings = get_settings()


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
            logger.warning("Embedder warmup failed: %s", exc)
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


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "srmap-rag-api",
        "docs": "/docs",
        "health": "/health",
        "chat_stream": "/chat/stream",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        with get_conn() as conn:
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return HealthResponse(status="ok", documents=int(docs), chunks=int(chunks))
    except Exception as exc:  # noqa: BLE001
        return HealthResponse(status="degraded", details={"error": str(exc)})


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
    return [{"role": m.role, "content": m.content} for m in req.history]


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if req.stream:
        raise HTTPException(status_code=400, detail="Use /chat/stream for streaming")

    history = _history_dicts(req)
    greeting = is_greeting(req.message)
    if greeting:
        rewritten, intent, chunks, citations = req.message, "chitchat", [], []
    else:
        rewritten, intent = rewrite_query(req.message, history, settings)
        chunks = hybrid_search(rewritten, intent=intent, settings=settings)
        _, citations = build_context(chunks)

    answer, citations = generate_answer(
        req.message, chunks, history=history, settings=settings, stream=False
    )
    assert isinstance(answer, str)
    if greeting:
        citations = []
    conv_id = req.conversation_id or str(uuid.uuid4())

    # Persist lightly
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id) VALUES (%s::uuid)
                ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                """,
                (conv_id,),
            )
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content)
                VALUES (%s::uuid, 'user', %s)
                """,
                (conv_id, req.message),
            )
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, retrieved_chunk_ids, citations)
                VALUES (%s::uuid, 'assistant', %s, %s, %s::jsonb)
                """,
                (
                    conv_id,
                    answer,
                    [c.chunk_id for c in chunks],
                    json.dumps(citations),
                ),
            )
    except Exception:
        pass

    return ChatResponse(
        conversation_id=conv_id,
        answer=answer,
        citations=[Citation(**c) for c in citations],
        intent=intent,
        rewritten_query=rewritten,
    )


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> EventSourceResponse:
    history = _history_dicts(req)
    greeting = is_greeting(req.message)
    if greeting:
        rewritten, intent, chunks, citations = req.message, "chitchat", [], []
    else:
        try:
            rewritten, intent = rewrite_query(req.message, history, settings)
            chunks = hybrid_search(rewritten, intent=intent, settings=settings)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Retrieval failed: {exc}") from exc
        _, citations = build_context(chunks)

    conv_id = req.conversation_id or str(uuid.uuid4())

    stream = generate_answer(req.message, chunks, history=history, settings=settings, stream=True)

    async def event_gen():
        yield {
            "event": "meta",
            "data": json.dumps(
                {
                    "conversation_id": conv_id,
                    "intent": intent,
                    "rewritten_query": rewritten,
                    "citations": citations,
                }
            ),
        }
        full: list[str] = []
        try:
            for piece in stream:  # type: ignore[union-attr]
                full.append(piece)
                yield {"event": "token", "data": json.dumps({"text": piece})}
        except Exception:  # noqa: BLE001
            # Keep SSE alive with a user-facing message (e.g. Gemini 503).
            msg = (
                "The chat model is temporarily busy (high demand). "
                "Please try again in a few seconds."
            )
            if not full:
                full.append(msg)
                yield {"event": "token", "data": json.dumps({"text": msg})}
            else:
                yield {
                    "event": "token",
                    "data": json.dumps({"text": f"\n\n({msg})"}),
                }
        answer = "".join(full)
        try:
            with get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO conversations (id) VALUES (%s::uuid)
                    ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                    """,
                    (conv_id,),
                )
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES (%s::uuid, 'user', %s)",
                    (conv_id, req.message),
                )
                conn.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content, retrieved_chunk_ids, citations)
                    VALUES (%s::uuid, 'assistant', %s, %s, %s::jsonb)
                    """,
                    (conv_id, answer, [c.chunk_id for c in chunks], json.dumps(citations)),
                )
        except Exception:
            pass
        yield {"event": "done", "data": json.dumps({"conversation_id": conv_id})}

    return EventSourceResponse(event_gen())


@app.post("/feedback")
def feedback(req: FeedbackRequest) -> dict[str, str]:
    try:
        with get_conn() as conn:
            if req.message_id:
                conn.execute(
                    "INSERT INTO feedback (message_id, rating, comment) VALUES (%s, %s, %s)",
                    (req.message_id, req.rating, req.comment),
                )
            else:
                # Store against latest assistant message in conversation if possible
                if req.conversation_id:
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
