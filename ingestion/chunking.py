from __future__ import annotations

import re
from typing import Any

from ingestion.models import Chunk, ParsedDocument

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))

except Exception:  # noqa: BLE001

    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


HEADING_SPLIT = re.compile(r"(?m)^(#{1,4}\s+.+)$")


def contextual_header(doc: ParsedDocument, heading: str | None) -> str:
    bits = [
        doc.title or "Untitled",
        heading or "",
        f"type={doc.doc_type}",
    ]
    if doc.modified_at:
        bits.append(f"modified={doc.modified_at.date().isoformat()}")
    if doc.school:
        bits.append(f"school={doc.school}")
    bits.append(f"url={doc.url}")
    return " | ".join(b for b in bits if b)


def _split_by_tokens(text: str, max_tokens: int, overlap: int) -> list[str]:
    if count_tokens(text) <= max_tokens:
        return [text]
    # Split on paragraphs first
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for p in paras:
        pt = count_tokens(p)
        if pt > max_tokens:
            # hard split by sentences/words
            words = p.split()
            cur: list[str] = []
            cur_t = 0
            for w in words:
                wt = count_tokens(w) + 1
                if cur_t + wt > max_tokens and cur:
                    chunks.append(" ".join(cur))
                    # overlap
                    overlap_words = cur[-max(1, overlap // 2) :]
                    cur = overlap_words
                    cur_t = count_tokens(" ".join(cur))
                cur.append(w)
                cur_t += wt
            if cur:
                chunks.append(" ".join(cur))
            continue
        if buf_tokens + pt > max_tokens and buf:
            chunks.append("\n\n".join(buf))
            # overlap: keep last paragraph if small
            if overlap > 0 and buf:
                last = buf[-1]
                buf = [last] if count_tokens(last) < overlap else []
                buf_tokens = count_tokens("\n\n".join(buf)) if buf else 0
            else:
                buf, buf_tokens = [], 0
        buf.append(p)
        buf_tokens += pt
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def chunk_document(
    doc: ParsedDocument,
    *,
    max_tokens: int = 600,
    overlap_tokens: int = 90,
) -> list[Chunk]:
    """Heading-aware chunking with contextual headers."""
    units: list[tuple[str, str]] = []
    if doc.sections:
        for sec in doc.sections:
            heading = sec.get("heading") or "Body"
            text = (sec.get("text") or "").strip()
            if text:
                units.append((heading, text))
    else:
        units.append(("Body", doc.text))

    # Short structured records: keep almost whole
    if doc.doc_type in ("faculty", "programme", "placement") and count_tokens(doc.text) <= max_tokens * 1.5:
        header = contextual_header(doc, None)
        body = f"{header}\n\n{doc.text}"
        return [
            Chunk(
                ord=0,
                heading_path=doc.title,
                text=body,
                token_count=count_tokens(body),
                metadata={"doc_type": doc.doc_type, "url": doc.url},
            )
        ]

    out: list[Chunk] = []
    ord_i = 0
    for heading, text in units:
        pieces = _split_by_tokens(text, max_tokens, overlap_tokens)
        for piece in pieces:
            header = contextual_header(doc, heading)
            body = f"{header}\n\n{piece}"
            out.append(
                Chunk(
                    ord=ord_i,
                    heading_path=heading,
                    text=body,
                    token_count=count_tokens(body),
                    metadata={
                        "doc_type": doc.doc_type,
                        "url": doc.url,
                        "title": doc.title,
                        "is_stale": doc.is_stale,
                    },
                )
            )
            ord_i += 1
    return out
