from __future__ import annotations

from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "skills" / "writing-bot" / "SKILL.md"


def test_writing_skill_contains_dissertation_defense_bilingual_contract() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "## Dissertation Defense Korean-English Output Contract" in text
    assert "## Korean" in text
    assert "## English" in text
    assert "## Terminology / Claim-Boundary Notes" in text
    assert "Do not make Korean wording more confident" in text
    assert "Under SCRIPT_NO_GO" in text
    assert "prepared_answer" in text
    assert "IEEE 519 준수 입증" in text
    assert "최종 계통계획안" in text
