import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

from checks import citation_problems, local_answer_kind, ungrounded_facts


def test_citation_and_grounding_checks():
    citations = [{"index": 1, "url": "https://www.srmap.edu.in/admission/seas-btech-tuition-fee/"}]
    answer = "CSE tuition is ₹4,00,000 [1]. Contact info@srmap.edu.in or 080-6988-6999 [9]."
    sources = "CSE ₹4,00,000 per year. Email info@srmap.edu.in. Phone 080-6988-6999."
    assert citation_problems(answer, citations) == ["[9]"]
    assert ungrounded_facts(answer, sources) == []
    assert ungrounded_facts("The fee is ₹9,99,999 [1].", sources) == ["₹9,99,999"]


def test_golden_local_cases_do_not_need_a_model():
    golden = json.loads((ROOT / "eval" / "golden_questions.json").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in golden}
    assert local_answer_kind(by_id["greeting-hi"]["question"]) == "greeting"
    assert local_answer_kind(by_id["not-found-empty"]["question"]) == "not_found"
