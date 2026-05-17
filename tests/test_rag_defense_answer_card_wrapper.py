from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "writing-bot" / "scripts" / "rag_defense_answer_card.py"
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


def test_no_ledger_blocks_final_answer() -> None:
    result = run_wrapper("--question", "Generate a final answer from raw retrieved chunks.", "--prepared-answer", "unsafe")
    assert result["gate"] == "blocked"
    assert result["final_prose_allowed"] is False
    assert result["prepared_answer"] is None
    assert result["raw_chunks_allowed"] is False
    assert "Build claim ledger rows" in result["answer_card"]["blocked_items_as_actions"][0]


def test_forbidden_answer_exists_under_script_no_go(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(
            [
                {
                    "claim_id": "CLM-001",
                    "claim_text": "Bounded traceability claim.",
                    "claim_type": "committee_answer",
                    "gate": "needs_human_judgment",
                    "script_status_checked": True,
                    "script_status": "SCRIPT_NO_GO",
                    "script_go_dependency": True,
                    "evidence_state": "main_active",
                    "existence_status": "exists",
                    "support_label": "partial_support",
                    "proximity_label": "same_sentence",
                    "entailment_label": "partially_entailed",
                    "source_authority": "internal_truth",
                    "source_penalty": [],
                    "main_active_evidence": ["kpg_540_20_13_7_funnel"],
                    "backup_only_evidence": [],
                    "blocked_evidence": [],
                    "safe_boundary_note": "Use bounded traceability wording only.",
                    "forbidden_answer": ["Do not claim final utility planning."],
                    "required_action": [],
                    "final_use_allowed": False,
                    "slide_use_allowed": False,
                    "committee_risk": "high",
                }
            ]
        ),
        encoding="utf-8",
    )
    result = run_wrapper("--question", "Give a polished committee answer.", "--ledger", str(ledger), "--prepared-answer", "unsafe")
    assert result["gate"] == "blocked"
    assert result["answer_card"]["forbidden_answer"]
    assert result["prepared_answer"] is None
    assert result["final_prose_allowed"] is False


def test_blocked_evidence_is_action_only() -> None:
    result = run_wrapper("--question", "Use the 1,440 result in a final answer.")
    assert result["gate"] == "blocked"
    assert result["prepared_answer"] is None
    assert result["evidence_decision"]["status"] == "blocked"
