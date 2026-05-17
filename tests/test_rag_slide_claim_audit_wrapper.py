from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "writing-bot" / "scripts" / "rag_slide_claim_audit.py"
DEFENSE_ROOT = Path(os.environ.get("DEFENSE_RAG_ROOT", "/Users/beopsookim/Desktop/work/Dissertation_Works_active/Defense_RAG"))


def run_wrapper(*args: str) -> dict:
    if not DEFENSE_ROOT.exists():
        pytest.skip("DEFENSE_RAG_ROOT is not available for wrapper integration test.")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--defense-root", str(DEFENSE_ROOT), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(proc.stdout)


def test_slide_title_validation_is_blocked(tmp_path: Path) -> None:
    slides = tmp_path / "slides.md"
    slides.write_text("# Validated Hybrid AC/DC Planning Framework\n\n- Complete validation of PF, fault, and harmonic analysis.\n", encoding="utf-8")
    result = run_wrapper("--slides", str(slides))
    assert result["gate"] == "blocked"
    assert result["final_prose_allowed"] is False
    assert result["prepared_answer"] is None
    assert result["raw_chunks_allowed"] is False
    assert result["slide_audit_report"]["slide_audit_cards"]
