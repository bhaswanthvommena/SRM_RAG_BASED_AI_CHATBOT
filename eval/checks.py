"""Deterministic checks for retrieval, citations, grounding, and failure answers.

These do not call a second model. Greeting and empty-retrieval answers come from
the local templates in app.generation.
"""

from __future__ import annotations

import re
from typing import Any

CITE_RE = re.compile(r"\[(\d+)\]")
RUPEE_RE = re.compile(r"₹\s?\d[\d,]*(?:\.\d+)?")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PHONE_RE = re.compile(r"\b\d{3}[\s-]?\d{3,4}[\s-]?\d{3,4}\b")


def citation_problems(answer: str, citations: list[dict[str, Any]]) -> list[str]:
    known = {int(item.get("index", 0)) for item in citations}
    return [f"[{n}]" for n in CITE_RE.findall(answer) if int(n) not in known]


def _compact(value: str) -> str:
    return value.lower().replace(" ", "").replace(",", "")


def ungrounded_facts(answer: str, sources: str) -> list[str]:
    haystack = _compact(sources)
    missing: list[str] = []
    for pattern in (RUPEE_RE, EMAIL_RE, PHONE_RE):
        for match in pattern.findall(answer):
            if _compact(match) not in haystack:
                missing.append(match)
    return missing


def local_answer_kind(question: str) -> str:
    """Classify answers that do not need retrieval or a chat model."""
    from app.generation import generate_answer, is_greeting

    if is_greeting(question):
        text, citations = generate_answer(question, [], stream=False)
        if citations:
            return "greeting_with_citations"
        if not isinstance(text, str) or not text.strip():
            return "greeting_empty"
        return "greeting"

    text, _citations = generate_answer(question, [], stream=False)
    if isinstance(text, str) and "could not find" in text.lower():
        return "not_found"
    return "unexpected"
