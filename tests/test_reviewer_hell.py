from __future__ import annotations

from pathlib import Path

from research_rag.reviewer_hell import (
    attach_artifact,
    build_advisor_shield,
    build_student_defense,
    merge_critique_candidates,
)


def sample_candidates() -> dict:
    return {
        "version": "critique_candidates_v1",
        "items": [
            {
                "candidate_id": "R1-C01",
                "reviewer_persona": "math_rigor",
                "severity": "major",
                "issue_axis": "validation",
                "target_claim": "The controller is validated with sufficient transient response evidence.",
                "criticism": "The transient response evidence is insufficient.",
                "required_action_type": "new_simulation",
                "requires_new_evidence": True,
                "blocked_until": ["human_interpretation", "simulation_result_file"],
                "fabrication_risk": "high",
                "student_allowed_response_mode": "blocked_until_human_task",
                "evidence_ids": ["E01"],
                "student_may_not_claim": ["Do not claim 50 ms response without interpreted artifacts."],
                "safe_response_boundary": "Only state that additional transient validation is needed.",
                "human_task": {"needed": True, "task": "Run transient simulation.", "expected_artifact": "CSV"},
            },
            {
                "candidate_id": "R3-C01",
                "reviewer_persona": "experiment_skeptic",
                "severity": "blocker",
                "issue_axis": "validation",
                "target_claim": "The controller is validated with sufficient transient response evidence.",
                "criticism": "A baseline transient comparison is missing.",
                "required_action_type": "new_simulation",
                "requires_new_evidence": True,
                "blocked_until": ["human_interpretation", "simulation_result_file"],
                "fabrication_risk": "high",
                "student_allowed_response_mode": "blocked_until_human_task",
                "evidence_ids": ["E02"],
                "student_may_not_claim": ["Do not claim new baseline results without interpreted artifacts."],
                "safe_response_boundary": "Only state that additional baseline validation is needed.",
                "human_task": {"needed": True, "task": "Run baseline simulation.", "expected_artifact": "CSV"},
            },
        ],
    }


def test_duplicate_critique_merges() -> None:
    pack = merge_critique_candidates(sample_candidates())
    assert pack["candidate_count"] == 2
    assert pack["merged_count"] == 1
    item = pack["items"][0]
    assert item["severity"] == "blocker"
    assert item["related_personas"] == ["experiment_skeptic"]
    assert item["merged_from"] == ["R1-C01", "R3-C01"]


def test_uninterpreted_artifact_does_not_unlock(tmp_path: Path) -> None:
    pack = merge_critique_candidates(sample_candidates())
    artifact = tmp_path / "result.csv"
    artifact.write_text("t,response\n0,0\n0.05,1\n", encoding="utf-8")
    attach_artifact(
        tmp_path,
        critique_id="C001",
        file_path=artifact,
        artifact_type="simulation_result",
        human_interpretation=None,
        interpretation_limits=None,
        supports_claim=None,
    )
    from research_rag.reviewer_hell import load_artifacts

    rebuttal = build_student_defense(pack, load_artifacts(tmp_path))
    assert rebuttal["responses"][0]["response_status"] == "blocked"
    assert rebuttal["responses"][0]["artifact_interpretation_used"] is False


def test_researcher_memo_unlocks_draft_use(tmp_path: Path) -> None:
    pack = merge_critique_candidates(sample_candidates())
    artifact = tmp_path / "result.csv"
    artifact.write_text("t,response\n0,0\n0.05,1\n", encoding="utf-8")
    attach_artifact(
        tmp_path,
        critique_id="C001",
        file_path=artifact,
        artifact_type="simulation_result",
        researcher_memo="The response settles within 50 ms for this scenario.",
        interpretation_limits="Only this operating point is covered.",
        supports_claim="Transient response is acceptable in the tested scenario.",
        metric="settling_time",
        metric_value=0.05,
        unit="s",
    )
    from research_rag.reviewer_hell import load_artifacts

    rebuttal = build_student_defense(pack, load_artifacts(tmp_path))
    response = rebuttal["responses"][0]
    assert response["response_status"] == "usable_for_draft"
    assert response["used_artifact_ids"] == ["A001"]
    assert response["researcher_memo_used"] is True


def test_metric_sanity_flags_need_human_judgment(tmp_path: Path) -> None:
    pack = merge_critique_candidates(sample_candidates())
    artifact = tmp_path / "result.csv"
    artifact.write_text("t,response\n0,0\n0.05,1\n", encoding="utf-8")
    attach_artifact(
        tmp_path,
        critique_id="C001",
        file_path=artifact,
        artifact_type="simulation_result",
        researcher_memo="The response improves relative to the baseline.",
        interpretation_limits="The baseline comparison still needs review.",
        supports_claim="Transient response improves.",
        metric="settling_time",
        metric_value=0.05,
        threshold=0.1,
    )
    from research_rag.reviewer_hell import load_artifacts

    rebuttal = build_student_defense(pack, load_artifacts(tmp_path))
    response = rebuttal["responses"][0]
    assert response["response_status"] == "needs_human_judgment"
    assert "unit_missing" in response["metric_sanity_flags"]
    assert "baseline_missing_for_improvement_claim" in response["metric_sanity_flags"]


def test_advisor_blocked_uses_humble_mode() -> None:
    pack = merge_critique_candidates(sample_candidates())
    rebuttal = build_student_defense(pack, None)
    advisor = build_advisor_shield(rebuttal, stop_on_blocked=True)
    response = advisor["responses"][0]
    assert advisor["ready_for_researcher_review"] is False
    assert response["tone_mode"] == "action item only"
    assert response["included_in_rebuttal"] is False
    assert advisor["researcher_action_items"]

