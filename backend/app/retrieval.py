from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from pgvector import Vector

from app.config import Settings, get_settings
from app.db import get_conn
from app.embeddings import active_model_id, get_embedder

DOC_TYPE_BOOSTS: dict[str, dict[str, float]] = {
    "fees": {"page": 1.3, "pdf": 1.4, "programme": 1.2},
    "admission": {"page": 1.3, "programme": 1.4, "pdf": 1.2},
    "faculty": {"faculty": 1.6, "staff": 1.2, "leadership": 1.2},
    "programme": {"programme": 1.6, "page": 1.1},
    "news": {"news": 1.4, "press_release": 1.2},
    # News/press often carry real LPA / offer numbers; CPT shells can be thin
    "placement": {"news": 1.5, "press_release": 1.4, "placement": 1.3, "page": 1.2, "pdf": 1.2},
    "general": {},
}


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    text: str
    heading_path: str | None
    url: str
    title: str | None
    doc_type: str
    score: float
    is_stale: bool
    modified_at: str | None


def rrf_fuse(
    ranked_lists: list[list[int]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    weights = weights or [1.0] * len(ranked_lists)
    for w, ranked in zip(weights, ranked_lists, strict=True):
        for rank, cid in enumerate(ranked):
            scores[cid] = scores.get(cid, 0.0) + w * (1.0 / (k + rank + 1))
    return scores


def detect_intent(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ("fee", "fees", "tuition", "scholarship", "cost")):
        return "fees"
    if any(w in q for w in ("admit", "admission", "eligibility", "srmjeee", "apply", "entrance")):
        return "admission"
    if any(w in q for w in ("faculty", "professor", "teacher", "dean", "hod")):
        return "faculty"
    if any(w in q for w in ("programme", "program", "course", "b.tech", "btech", "mba", "m.tech", "phd", "curriculum")):
        return "programme"
    if any(w in q for w in ("placement", "salary", "recruit", "package", "lpa")):
        return "placement"
    if any(w in q for w in ("news", "event", "recent", "latest")):
        return "news"
    return "general"


def rewrite_query(question: str, history: list[dict[str, str]], settings: Settings) -> tuple[str, str]:
    """Return (standalone_query, intent). Uses Gemini when key present; else heuristic."""
    intent = detect_intent(question)
    if not history or not settings.chat_api_key:
        return question, intent

    from google import genai

    client = genai.Client(api_key=settings.chat_api_key)
    hist = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    prompt = (
        "Rewrite the latest user question into a standalone search query about SRM University-AP. "
        "Return ONLY the rewritten query.\n\n"
        f"Chat history:\n{hist}\n\nLatest question: {question}"
    )
    try:
        resp = client.models.generate_content(model=settings.gemini_chat_model, contents=prompt)
        rewritten = (resp.text or question).strip().strip('"')
        intent = detect_intent(rewritten + " " + question)
        return rewritten or question, intent
    except Exception as exc:
        from app.observe import log_event

        log_event(
            "query_rewrite",
            labels={
                "route": "rewrite",
                "status_class": "5xx",
                "error_category": "generation_failed",
                "intent": intent,
            },
            error_type=type(exc).__name__,
        )
        return question, intent


def hybrid_search(
    query: str,
    *,
    intent: str = "general",
    top_k: int = 8,
    settings: Settings | None = None,
) -> list[RetrievedChunk]:
    settings = settings or get_settings()
    embedder = get_embedder()
    embed_started = time.perf_counter()
    qvec = embedder.embed_query(query)
    from app.observe import record_timing

    record_timing("embed_ms", (time.perf_counter() - embed_started) * 1000)
    model_id = active_model_id(settings)

    with get_conn(settings) as conn:
        vec_rows = conn.execute(
            """
            SELECT c.id
            FROM chunks c
            WHERE c.embedding IS NOT NULL
              AND c.embed_model = %s
            ORDER BY c.embedding <=> %s
            LIMIT 30
            """,
            (model_id, Vector(qvec)),
        ).fetchall()
        vec_ids = [int(r[0]) for r in vec_rows]

        fts_rows = conn.execute(
            """
            SELECT c.id
            FROM chunks c
            WHERE c.tsv @@ plainto_tsquery('english', %s)
            ORDER BY ts_rank_cd(c.tsv, plainto_tsquery('english', %s)) DESC
            LIMIT 30
            """,
            (query, query),
        ).fetchall()
        fts_ids = [int(r[0]) for r in fts_rows]

        fused = rrf_fuse([vec_ids, fts_ids], weights=[1.0, 1.0])
        if not fused:
            return []

        # Fetch details for candidates
        ids = list(fused.keys())
        rows = conn.execute(
            """
            SELECT c.id, c.document_id, c.text, c.heading_path, c.metadata,
                   d.url, d.title, d.doc_type, d.is_stale, d.modified_at
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id = ANY(%s)
            """,
            (ids,),
        ).fetchall()

    boosts = DOC_TYPE_BOOSTS.get(intent, {})
    results: list[RetrievedChunk] = []
    for r in rows:
        cid = int(r[0])
        text = r[2] or ""
        doc_type = r[7] or "page"
        is_stale = bool(r[8])
        score = fused.get(cid, 0.0) * boosts.get(doc_type, 1.0)
        if is_stale:
            score *= 0.7
        # Prefer substantive chunks; downrank thin shells / stubs
        if len(text) < 250 or "thin_shell" in text.lower() or "full programme details may live" in text.lower():
            score *= 0.55
        elif any(tok in text.lower() for tok in ("lpa", "salary", "offer", "recruiter", "fee", "eligibility")):
            score *= 1.15
        modified = r[9].isoformat() if r[9] else None
        results.append(
            RetrievedChunk(
                chunk_id=cid,
                document_id=int(r[1]),
                text=text,
                heading_path=r[3],
                url=r[5],
                title=r[6],
                doc_type=doc_type,
                score=score,
                is_stale=is_stale,
                modified_at=modified,
            )
        )

    results.sort(key=lambda x: x.score, reverse=True)
    # One chunk per URL so the UI doesn't show duplicate year pages
    diversified: list[RetrievedChunk] = []
    seen_urls: set[str] = set()
    for chunk in results:
        key = (chunk.url or "").rstrip("/").lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        diversified.append(chunk)
        if len(diversified) >= top_k:
            break
    return diversified


def build_context(chunks: list[RetrievedChunk]) -> tuple[str, list[dict[str, Any]]]:
    citations: list[dict[str, Any]] = []
    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        citations.append(
            {
                "index": i,
                "chunk_id": c.chunk_id,
                "url": c.url,
                "title": c.title,
                "doc_type": c.doc_type,
                "modified_at": c.modified_at,
                "is_stale": c.is_stale,
            }
        )
        stale_note = " [STALE CONTENT]" if c.is_stale else ""
        parts.append(
            f"[{i}]{stale_note} Title: {c.title or 'Untitled'}\n"
            f"URL: {c.url}\n"
            f"Modified: {c.modified_at or 'unknown'}\n"
            f"{c.text}"
        )
    return "\n\n---\n\n".join(parts), citations
