from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    stream: bool = True


class Citation(BaseModel):
    index: int
    chunk_id: int
    url: str
    title: str | None = None
    doc_type: str | None = None
    modified_at: str | None = None
    is_stale: bool = False


class ChatResponse(BaseModel):
    conversation_id: str | None = None
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    intent: str | None = None
    rewritten_query: str | None = None


class FeedbackRequest(BaseModel):
    message_id: int | None = None
    rating: Literal[-1, 1]
    comment: str | None = None
    conversation_id: str | None = None
    answer_preview: str | None = None


class HealthResponse(BaseModel):
    status: str
    documents: int = 0
    chunks: int = 0
    details: dict[str, Any] = Field(default_factory=dict)
