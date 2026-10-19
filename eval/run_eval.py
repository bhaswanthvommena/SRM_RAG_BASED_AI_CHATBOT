"""Retrieval eval against golden questions.

Local greeting and empty-retrieval cases do not need the database or a model.
Pass --generate to score citations and grounded fees, phones, and emails on
retrieved chunks with the existing chat model. This does not add a judge model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.generation import generate_answer  # noqa: E402
from app.retrieval import build_context, hybrid_search, rewrite_query  # noqa: E402
from checks import citation_problems, local_answer_kind, ungrounded_facts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Score citations and grounding with the existing chat model",
    )
    args = parser.parse_args()
    golden = json.loads((Path(__file__).parent / "golden_questions.json").read_text(encoding="utf-8"))
    settings = get_settings()
    hits = 0
    checked = 0
    local_hits = 0
    local_total = 0

    for item in golden:
        expect = item.get("expect")
        if expect in {"greeting", "not_found"}:
            local_total += 1
            kind = local_answer_kind(item["question"])
            ok = kind == expect
            local_hits += int(ok)
            print(f"[{'HIT' if ok else 'MISS'}] {item['id']} expect={expect} got={kind}")
            continue

        q = item["question"]
        rewritten, intent = rewrite_query(q, [], settings)
        chunks = hybrid_search(rewritten, intent=intent or item.get("intent", "general"), settings=settings)
        needles = item.get("must_cite_url_contains") or []
        if not needles:
            print(f"[skip-check] {item['id']}: retrieved {len(chunks)}")
        else:
            checked += 1
            urls = " ".join((c.url or "").lower() for c in chunks)
            ok = any(n.lower() in urls for n in needles)
            hits += int(ok)
            status = "HIT" if ok else "MISS"
            print(f"[{status}] {item['id']} intent={intent} n={len(chunks)}")
            for chunk in chunks[:3]:
                print(f"   - {chunk.url}")

        if args.generate and chunks:
            answer, citations = generate_answer(q, chunks, history=[], settings=settings, stream=False)
            assert isinstance(answer, str)
            problems = citation_problems(answer, citations)
            _context, _cites = build_context(chunks)
            missing = ungrounded_facts(answer, "\n".join(chunk.text for chunk in chunks))
            if problems or missing:
                print(f"   answer-issues citations={problems} ungrounded={missing}")
            else:
                print("   answer-ok")

    print(f"\nURL hint hit-rate: {hits}/{checked}")
    print(f"Local failure cases: {local_hits}/{local_total}")


if __name__ == "__main__":
    main()
