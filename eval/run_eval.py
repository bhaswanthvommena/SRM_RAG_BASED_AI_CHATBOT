"""Simple retrieval eval against golden questions (requires loaded DB + API key)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.retrieval import hybrid_search, rewrite_query  # noqa: E402


def main() -> None:
    golden = json.loads((Path(__file__).parent / "golden_questions.json").read_text(encoding="utf-8"))
    settings = get_settings()
    hits = 0
    total = 0

    for item in golden:
        q = item["question"]
        rewritten, intent = rewrite_query(q, [], settings)
        chunks = hybrid_search(rewritten, intent=intent or item.get("intent", "general"), settings=settings)
        total += 1
        needles = item.get("must_cite_url_contains") or []
        if not needles:
            print(f"[skip-check] {item['id']}: retrieved {len(chunks)}")
            continue
        urls = " ".join((c.url or "").lower() for c in chunks)
        ok = any(n.lower() in urls for n in needles)
        hits += int(ok)
        status = "HIT" if ok else "MISS"
        print(f"[{status}] {item['id']} intent={intent} n={len(chunks)}")
        for c in chunks[:3]:
            print(f"   - {c.url}")

    print(f"\nURL hint hit-rate: {hits}/{total}")


if __name__ == "__main__":
    main()
