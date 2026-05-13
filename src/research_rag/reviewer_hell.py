from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CRITIQUE_CANDIDATES_VERSION = "critique_candidates_v1"
CRITIQUE_PACK_VERSION = "critique_pack_v1"
REBUTTAL_DRAFT_VERSION = "rebuttal_draft_v1"
HUMAN_ARTIFACTS_VERSION = "human_artifacts_v1"
ADVISOR_SHIELD_VERSION = "advisor_shield_v1"
REVIEW_STATUS_USABLE = "usable_for_draft"
REVIEW_STATUS_NEEDS_HUMAN = "needs_human_judgment"
REVIEW_STATUS_BLOCKED = "blocked"

REVIEWER_PERSONAS = {
    "math_rigor": "Formulation, constraints, assumptions, proof, and mathematical consistency.",
    "literature_sota": "Missing recent work, standards, source authority, and related-work positioning.",
    "experiment_skeptic": "Validation, baselines, reproducibility, ablations, and evidence sufficiency.",
    "logic_gap_detector": "Claim boundary, causal leaps, overclaiming, and unstated assumptions.",
    "practicality_reviewer": "Grid deployment relevance, operating constraints, scalability, and engineering value.",
}

SEVERITY_RANK = {"minor": 1, "major": 2, "blocker": 3}
ACTION_ARTIFACT_TYPES = {
    "simulation_result_file": {"simulation_result"},
    "figure_or_table_id": {"figure", "table"},
    "human_interpretation": {"simulation_result", "figure", "table", "code_commit", "methods_note", "advisor_decision"},
    "researcher_memo": {"simulation_result", "figure", "table", "code_commit", "methods_note", "advisor_decision"},
    "code_commit": {"code_commit"},
    "methods_note": {"methods_note"},
    "advisor_decision": {"advisor_decision"},
}
BANNED_BLOCKED_PHRASES = (
    "we demonstrate",
    "our results prove",
    "as shown in figure",
    "as shown in fig.",
)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_signature(*parts: Any) -> str:
    raw = " ".join(clean_text(part).lower() for part in parts if clean_text(part))
    raw = re.sub(r"[^a-z0-9가-힣]+", "_", raw).strip("_")
    raw = re.sub(r"_+", "_", raw)
    return raw[:96] or "unspecified_issue"


def token_set(text: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-z0-9가-힣]+", text.lower()) if len(tok) >= 3}


def jaccard(a: str, b: str) -> float:
    left = token_set(a)
    right = token_set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def severity_max(values: list[str]) -> str:
    return max(values or ["minor"], key=lambda value: SEVERITY_RANK.get(value, 0))


def evidence_status_from_pack(evidence_pack: dict[str, Any] | None) -> str:
    if not evidence_pack:
        return "missing"
    items = evidence_pack.get("items") or []
    if not items:
        return "missing"
    qualities = {str(item.get("source_quality") or "") for item in items}
    if "verified" in qualities:
        return "partial"
    if "metadata-only" in qualities:
        return "metadata-only"
    return "partial"


def build_review_attack_prompt(draft_text: str, evidence_pack: dict[str, Any] | None) -> str:
    evidence_summary = []
    for item in (evidence_pack or {}).get("items", [])[:8]:
        evidence_summary.append(
            {
                "evidence_id": item.get("evidence_id"),
                "title": item.get("title"),
                "source_quality": item.get("source_quality"),
                "page_range": item.get("page_range"),
                "supported_boundary": item.get("supported_boundary"),
                "evidence_gap": item.get("evidence_gap"),
            }
        )
    schema = {
        "candidate_id": "R1-C01",
        "reviewer_persona": "math_rigor",
        "severity": "blocker|major|minor",
        "issue_axis": "math|literature|validation|logic|practicality",
        "target_location": "section/page/paragraph if known",
        "target_claim": "specific vulnerable claim",
        "criticism": "hard reviewer criticism",
        "why_it_matters": "why this would matter to an IEEE reviewer",
        "evidence_ids": ["E01"],
        "evidence_status": "verified|partial|metadata-only|missing|not-applicable",
        "requires_new_evidence": True,
        "required_action_type": "text_revision|additional_literature|analysis_rerun|new_simulation|new_experiment|new_figure_table|human_judgment",
        "fabrication_risk": "low|medium|high",
        "student_allowed_response_mode": "defend_existing|narrow_claim|acknowledge_limitation|propose_new_work|blocked_until_human_task",
        "blocked_until": ["researcher_memo", "simulation_result_file"],
        "student_may_not_claim": ["Do not claim new results without researcher memo and artifact evidence."],
        "safe_response_boundary": "maximum defensible response boundary",
        "human_task": {"needed": True, "task": "what a human must do", "expected_artifact": "required artifact"},
    }
    return "\n".join(
        [
            "# Reviewer Hell Candidate Generation",
            "",
            "Act as five IEEE Transactions reviewers, but output only JSON. Do not output chain-of-thought.",
            "Generate raw critique candidates first. Do not merge them yourself; Python will merge deterministically.",
            "Each persona may produce at most two major critique candidates, and should avoid repeating the same root cause when possible.",
            "",
            "## Personas",
            json.dumps(REVIEWER_PERSONAS, indent=2, ensure_ascii=False),
            "",
            "## Required JSON shape",
            json.dumps({"version": CRITIQUE_CANDIDATES_VERSION, "items": [schema]}, indent=2, ensure_ascii=False),
            "",
            "## Evidence summary",
            json.dumps(evidence_summary, indent=2, ensure_ascii=False),
            "",
            "## Draft",
            draft_text[:60000],
        ]
    )


def heuristic_candidates(draft_text: str, evidence_pack: dict[str, Any] | None) -> dict[str, Any]:
    text = clean_text(draft_text)
    lowered = text.lower()
    evidence_status = evidence_status_from_pack(evidence_pack)
    evidence_ids = [item.get("evidence_id") for item in (evidence_pack or {}).get("items", [])[:3] if item.get("evidence_id")]
    candidates: list[dict[str, Any]] = []

    def add(
        candidate_id: str,
        persona: str,
        severity: str,
        axis: str,
        claim: str,
        criticism: str,
        action: str,
        needs_evidence: bool,
        blocked_until: list[str],
        risk: str,
        mode: str,
        task: str,
        artifact: str,
    ) -> None:
        candidates.append(
            {
                "candidate_id": candidate_id,
                "reviewer_persona": persona,
                "severity": severity,
                "issue_axis": axis,
                "target_location": "draft-level heuristic scan",
                "target_claim": claim,
                "criticism": criticism,
                "why_it_matters": "A reviewer can reject or request major revision if this point remains unsupported.",
                "evidence_ids": evidence_ids,
                "evidence_status": evidence_status,
                "requires_new_evidence": needs_evidence,
                "required_action_type": action,
                "fabrication_risk": risk,
                "student_allowed_response_mode": mode,
                "blocked_until": blocked_until,
                "student_may_not_claim": [
                    "Do not claim new numerical, simulation, or experimental results without researcher memo and artifact evidence."
                ],
                "safe_response_boundary": "Defend only what the draft and attached evidence directly support.",
                "human_task": {"needed": needs_evidence, "task": task, "expected_artifact": artifact},
            }
        )

    add(
        "R1-C01",
        "math_rigor",
        "major",
        "math",
        "The proposed method is mathematically well founded.",
        "The formulation, assumptions, and constraint definitions may not be explicit enough for a rigorous reviewer.",
        "text_revision",
        False,
        [],
        "medium",
        "narrow_claim",
        "Clarify equations, assumptions, and variable definitions.",
        "revised formulation text",
    )
    if "baseline" not in lowered and "ablation" not in lowered:
        add(
            "R3-C01",
            "experiment_skeptic",
            "blocker",
            "validation",
            "The method is validated convincingly against alternatives.",
            "The draft does not clearly show baselines or ablations sufficient to rule out simpler explanations.",
            "analysis_rerun",
            True,
            ["researcher_memo", "simulation_result_file"],
            "high",
            "blocked_until_human_task",
            "Provide baseline or ablation results with human interpretation.",
            "simulation result file and interpretation",
        )
    if any(term in lowered for term in ["novel", "first", "outperform", "superior"]):
        add(
            "R4-C01",
            "logic_gap_detector",
            "major",
            "logic",
            "The contribution is stronger than prior work.",
            "The wording may overstate novelty or superiority beyond the evidence currently shown.",
            "text_revision",
            False,
            [],
            "medium",
            "narrow_claim",
            "Narrow the contribution claim and align it with evidence.",
            "claim revision",
        )
    add(
        "R2-C01",
        "literature_sota",
        "major",
        "literature",
        "The related work establishes the current SOTA accurately.",
        "The draft needs an explicit check against recent HVDC/VSC-MTDC planning and standards literature.",
        "additional_literature",
        True,
            ["researcher_memo"],
        "medium",
        "propose_new_work",
        "Confirm and summarize the missing literature boundary.",
        "annotated literature notes",
    )
    add(
        "R5-C01",
        "practicality_reviewer",
        "major",
        "practicality",
        "The method is practically relevant to grid operation and planning.",
        "The engineering deployment assumptions and practical constraints may not be concrete enough.",
        "text_revision",
        False,
        [],
        "low",
        "acknowledge_limitation",
        "Clarify practical scope and deployment assumptions.",
        "scope note",
    )
    return {"version": CRITIQUE_CANDIDATES_VERSION, "created_at": iso_now(), "generator": "heuristic", "items": candidates}


def load_candidates(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if isinstance(payload.get("items"), list):
        return payload
    if isinstance(payload, list):
        return {"version": CRITIQUE_CANDIDATES_VERSION, "created_at": iso_now(), "items": payload}
    raise ValueError(f"Candidate JSON must contain an items list: {path}")


def parse_json_payload(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def run_candidate_adapter(model: str, prompt: str, *, parent_timeout_s: float) -> tuple[dict[str, Any] | None, list[str]]:
    env_name = f"RAG_{model.upper()}_CMD"
    raw_cmd = os.environ.get(env_name)
    if not raw_cmd:
        return None, [f"{model}_adapter_not_configured_prompt_only"]
    timeout_s = max(3.0, min(parent_timeout_s - 5.0, parent_timeout_s))
    try:
        cmd = shlex.split(raw_cmd, posix=os.name != "nt")
        result = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return None, [f"{model}_adapter_timeout"]
    except OSError as exc:
        return None, [f"{model}_adapter_failed:{exc}"]
    if result.returncode != 0:
        return None, [f"{model}_adapter_returncode_{result.returncode}"]
    try:
        payload = parse_json_payload(result.stdout)
    except json.JSONDecodeError:
        return None, [f"{model}_adapter_invalid_json"]
    if not isinstance(payload.get("items"), list):
        return None, [f"{model}_adapter_missing_items"]
    payload.setdefault("version", CRITIQUE_CANDIDATES_VERSION)
    payload.setdefault("created_at", iso_now())
    payload.setdefault("generator", model)
    return payload, []


def candidate_signature(item: dict[str, Any]) -> str:
    existing = clean_text(item.get("critique_signature"))
    if existing:
        return normalize_signature(existing)
    return normalize_signature(item.get("issue_axis"), item.get("required_action_type"), item.get("target_claim"))


def should_merge(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, str]:
    same_axis = clean_text(left.get("issue_axis")).lower() == clean_text(right.get("issue_axis")).lower()
    same_action = clean_text(left.get("required_action_type")).lower() == clean_text(right.get("required_action_type")).lower()
    claim_sim = jaccard(clean_text(left.get("target_claim")), clean_text(right.get("target_claim")))
    sig_sim = jaccard(candidate_signature(left), candidate_signature(right))
    if same_axis and same_action and claim_sim >= 0.35:
        return True, "same target claim, issue axis, and required action"
    if same_axis and sig_sim >= 0.55:
        return True, "similar critique signature within same issue axis"
    return False, ""


def merge_group(group: list[dict[str, Any]], index: int) -> dict[str, Any]:
    primary = max(group, key=lambda item: SEVERITY_RANK.get(str(item.get("severity") or "minor"), 0))
    personas = []
    merged_from = []
    blocked_until: list[str] = []
    evidence_ids: list[str] = []
    may_not_claim: list[str] = []
    for item in group:
        persona = clean_text(item.get("reviewer_persona")) or "unknown_reviewer"
        if persona not in personas:
            personas.append(persona)
        merged_from.append(clean_text(item.get("candidate_id")) or f"candidate_{len(merged_from) + 1}")
        for value in item.get("blocked_until") or []:
            if value not in blocked_until:
                blocked_until.append(value)
        for value in item.get("evidence_ids") or []:
            if value and value not in evidence_ids:
                evidence_ids.append(value)
        for value in item.get("student_may_not_claim") or []:
            if value and value not in may_not_claim:
                may_not_claim.append(value)

    severity = severity_max([str(item.get("severity") or "minor") for item in group])
    requires_new = any(bool(item.get("requires_new_evidence")) for item in group)
    human_needed = requires_new or any((item.get("human_task") or {}).get("needed") for item in group)
    result = dict(primary)
    result.update(
        {
            "critique_id": f"C{index:03d}",
            "reviewer_persona": personas[0],
            "related_personas": personas[1:],
            "merged_from": merged_from,
            "overlap_reason": " / ".join(
                sorted({clean_text(item.get("overlap_reason")) for item in group if clean_text(item.get("overlap_reason"))})
            )
            or ("single candidate" if len(group) == 1 else "deterministic merge by signature and target claim"),
            "critique_signature": candidate_signature(primary),
            "severity": severity,
            "evidence_ids": evidence_ids,
            "requires_new_evidence": requires_new,
            "blocked_until": blocked_until,
            "student_may_not_claim": may_not_claim
            or ["Do not claim new results without researcher memo and artifact evidence."],
            "human_task": {
                "needed": human_needed,
                "task": clean_text((primary.get("human_task") or {}).get("task")) or "Review and provide missing evidence if needed.",
                "expected_artifact": clean_text((primary.get("human_task") or {}).get("expected_artifact"))
                or "interpreted artifact or revision note",
            },
        }
    )
    return result


def merge_critique_candidates(candidates: dict[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in candidates.get("items") or []]
    groups: list[list[dict[str, Any]]] = []
    for item in items:
        item.setdefault("critique_signature", candidate_signature(item))
        placed = False
        for group in groups:
            merge, reason = should_merge(group[0], item)
            if merge:
                item["overlap_reason"] = reason
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    merged = [merge_group(group, idx) for idx, group in enumerate(groups, start=1)]
    return {
        "version": CRITIQUE_PACK_VERSION,
        "created_at": iso_now(),
        "source_candidates_version": candidates.get("version"),
        "candidate_count": len(items),
        "merged_count": len(merged),
        "items": merged,
    }


def critique_markdown(pack: dict[str, Any]) -> str:
    lines = ["# Reviewer Hell Critique Pack", "", f"- Version: {pack.get('version')}", f"- Items: {pack.get('merged_count', 0)}", ""]
    for item in pack.get("items", []):
        related = ", ".join(item.get("related_personas") or ["none"])
        lines.extend(
            [
                f"## {item.get('critique_id')}: {item.get('severity')} / {item.get('issue_axis')}",
                f"- Persona: {item.get('reviewer_persona')} (related: {related})",
                f"- Target claim: {item.get('target_claim')}",
                f"- Criticism: {item.get('criticism')}",
                f"- Action: {item.get('required_action_type')} / requires_new_evidence={item.get('requires_new_evidence')}",
                f"- Fabrication risk: {item.get('fabrication_risk')}",
                f"- Safe boundary: {item.get('safe_response_boundary')}",
                "",
            ]
        )
    return "\n".join(lines)


def write_review_attack_files(workspace: Path, prompt: str, candidates: dict[str, Any], pack: dict[str, Any]) -> dict[str, str]:
    paths = {
        "review_attack_prompt": workspace / "review_attack_prompt.md",
        "critique_candidates": workspace / "critique_candidates.json",
        "critique_pack": workspace / "critique_pack.json",
        "review_attack_summary": workspace / "review_attack_summary.md",
    }
    paths["review_attack_prompt"].write_text(prompt, encoding="utf-8")
    write_json(paths["critique_candidates"], candidates)
    write_json(paths["critique_pack"], pack)
    paths["review_attack_summary"].write_text(critique_markdown(pack), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def improvement_claim(text: str | None) -> bool:
    value = clean_text(text).lower()
    return bool(
        re.search(
            r"\b(improv|better|reduce|reduction|increase|decrease|lower|higher|faster|slower|less|more|outperform|superior)\b|개선|감소|증가|향상|우수",
            value,
        )
    )


def metric_summary(
    *,
    metric: str | None,
    metric_value: float | None,
    unit: str | None,
    baseline_value: float | None,
    threshold: float | None,
    threshold_source: str | None,
    threshold_direction: str | None,
    supports_claim: str | None,
) -> dict[str, Any]:
    flags: list[str] = []
    if metric and unit is None:
        flags.append("unit_missing")
    if threshold is not None and not threshold_source:
        flags.append("threshold_source_missing")
    if threshold is not None and not threshold_direction:
        flags.append("threshold_direction_ambiguous")
    if metric and baseline_value is None and improvement_claim(supports_claim):
        flags.append("baseline_missing_for_improvement_claim")
    if not metric:
        return {"provided": False, "sanity_flags": flags, "status": REVIEW_STATUS_USABLE}
    return {
        "provided": True,
        "metric": metric,
        "metric_value": metric_value,
        "unit": clean_text(unit),
        "baseline_value": baseline_value,
        "threshold": threshold,
        "threshold_source": clean_text(threshold_source),
        "threshold_direction": clean_text(threshold_direction),
        "sanity_flags": flags,
        "status": REVIEW_STATUS_NEEDS_HUMAN if flags else REVIEW_STATUS_USABLE,
    }


def artifact_review_status(researcher_memo: str | None, metric: dict[str, Any]) -> str:
    if not clean_text(researcher_memo):
        return REVIEW_STATUS_BLOCKED
    if metric.get("sanity_flags"):
        return REVIEW_STATUS_NEEDS_HUMAN
    return REVIEW_STATUS_USABLE


def load_artifacts(workspace: Path) -> dict[str, Any]:
    path = workspace / "human_artifacts.json"
    if path.exists():
        return read_json(path)
    return {"version": HUMAN_ARTIFACTS_VERSION, "created_at": iso_now(), "artifacts": []}


def attach_artifact(
    workspace: Path,
    *,
    critique_id: str,
    file_path: Path,
    artifact_type: str,
    researcher_memo: str | None = None,
    human_interpretation: str | None = None,
    interpretation_limits: str | None = None,
    supports_claim: str | None = None,
    metric: str | None = None,
    metric_value: float | None = None,
    unit: str | None = None,
    baseline_value: float | None = None,
    threshold: float | None = None,
    threshold_source: str | None = None,
    threshold_direction: str | None = None,
) -> dict[str, Any]:
    artifacts = load_artifacts(workspace)
    existing = artifacts.get("artifacts") or []
    artifact_id = f"A{len(existing) + 1:03d}"
    memo = clean_text(researcher_memo) or clean_text(human_interpretation)
    metrics = metric_summary(
        metric=metric,
        metric_value=metric_value,
        unit=unit,
        baseline_value=baseline_value,
        threshold=threshold,
        threshold_source=threshold_source,
        threshold_direction=threshold_direction,
        supports_claim=supports_claim,
    )
    review_status = artifact_review_status(memo, metrics)
    record = {
        "artifact_id": artifact_id,
        "critique_id": critique_id,
        "artifact_type": artifact_type,
        "path": str(file_path),
        "sha256": sha256_file(file_path),
        "researcher_memo": memo,
        "human_interpretation": memo,
        "interpretation_limits": clean_text(interpretation_limits),
        "supports_claim": clean_text(supports_claim),
        "interpretation_status": "researcher_memo_provided" if memo else "uninterpreted",
        "review_status": review_status,
        "metric_summary": metrics,
        "claim_boundary": {
            "support_type": "artifact" if memo else "manuscript_only",
            "safe_wording": clean_text(supports_claim) or memo,
            "risk_note": clean_text(interpretation_limits) or "Researcher review is required before submission.",
        },
        "attached_at": iso_now(),
    }
    existing.append(record)
    artifacts["artifacts"] = existing
    write_json(workspace / "human_artifacts.json", artifacts)
    augmented = {"version": "evidence_pack_augmented_v1", "created_at": iso_now(), "human_artifacts": existing}
    pack_path = workspace / "evidence_pack.json"
    if pack_path.exists():
        augmented["evidence_pack"] = read_json(pack_path)
    write_json(workspace / "evidence_pack_augmented.json", augmented)
    return record


def interpreted_artifacts_for(artifacts: dict[str, Any], critique_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in artifacts.get("artifacts", [])
        if item.get("critique_id") == critique_id
        and clean_text(item.get("researcher_memo") or item.get("human_interpretation"))
    ]


def blocked_requirements_satisfied(blocked_until: list[str], artifacts: list[dict[str, Any]]) -> bool:
    if not blocked_until:
        return True
    if not artifacts:
        return False
    for requirement in blocked_until:
        allowed = ACTION_ARTIFACT_TYPES.get(str(requirement), set())
        if requirement in {"human_interpretation", "researcher_memo"}:
            if not any(clean_text(item.get("researcher_memo") or item.get("human_interpretation")) for item in artifacts):
                return False
        elif allowed and not any(item.get("artifact_type") in allowed for item in artifacts):
            return False
    return True


def build_student_defense(critique_pack: dict[str, Any], artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    artifact_payload = artifacts or {"artifacts": []}
    responses: list[dict[str, Any]] = []
    claim_boundary_map: list[dict[str, Any]] = []
    for item in critique_pack.get("items", []):
        critique_id = item.get("critique_id")
        usable_artifacts = interpreted_artifacts_for(artifact_payload, str(critique_id))
        artifact_ok = blocked_requirements_satisfied(list(item.get("blocked_until") or []), usable_artifacts)
        metric_flags = sorted(
            {
                flag
                for artifact in usable_artifacts
                for flag in ((artifact.get("metric_summary") or {}).get("sanity_flags") or [])
            }
        )
        artifact_ids = [a.get("artifact_id") for a in usable_artifacts]
        researcher_memos = [
            clean_text(a.get("researcher_memo") or a.get("human_interpretation")) for a in usable_artifacts
        ]
        researcher_memos = [memo for memo in researcher_memos if memo]
        requires_new = bool(item.get("requires_new_evidence"))
        if requires_new and not artifact_ok:
            status = REVIEW_STATUS_BLOCKED
            advisor_strategy = "concede"
            blocked_reason = "Required evidence or researcher memo is missing."
            rebuttal = (
                "We appreciate the reviewer raising this point. The current manuscript cannot honestly defend this claim "
                "until the required artifact or researcher memo is provided."
            )
        elif metric_flags:
            status = REVIEW_STATUS_NEEDS_HUMAN
            advisor_strategy = "narrow"
            blocked_reason = "Metric sanity flags require researcher judgment before strong defense."
            rebuttal = (
                "We appreciate the reviewer raising this point. The attached artifact is useful for drafting, but the metric "
                f"sanity flags require researcher judgment before a strong rebuttal: {', '.join(metric_flags)}."
            )
        elif requires_new and artifact_ok:
            status = REVIEW_STATUS_USABLE
            advisor_strategy = "defend"
            blocked_reason = None
            interpretation = "; ".join(researcher_memos)
            rebuttal = (
                "We appreciate the reviewer raising this point. Based on the attached researcher memo, "
                f"the defensible response is: {interpretation}"
            )
        elif item.get("student_allowed_response_mode") == "narrow_claim":
            status = REVIEW_STATUS_USABLE
            advisor_strategy = "narrow"
            blocked_reason = None
            rebuttal = (
                "We agree that the claim should be scoped more carefully. We will revise the manuscript to align the "
                "claim with the evidence boundary rather than overstate the result."
            )
        else:
            status = REVIEW_STATUS_USABLE
            advisor_strategy = "defend"
            blocked_reason = None
            rebuttal = (
                "We appreciate the comment. The concern can be addressed by clarifying the manuscript text and tying the "
                "response to the existing evidence boundary."
            )
        support_type = "artifact" if artifact_ids else ("paper" if item.get("evidence_ids") else "manuscript_only")
        boundary = {
            "claim": item.get("target_claim") or item.get("criticism"),
            "support_type": support_type,
            "anchor": artifact_ids or item.get("evidence_ids") or [],
            "safe_wording": clean_text(item.get("safe_response_boundary"))
            or clean_text(item.get("target_claim"))
            or "Keep the claim bounded to the manuscript evidence.",
            "risk_note": "; ".join(metric_flags)
            or clean_text(item.get("evidence_status"))
            or "Researcher review is required before submission.",
        }
        claim_boundary_map.append({"critique_id": critique_id, **boundary})
        responses.append(
            {
                "critique_id": critique_id,
                "original_criticism": item.get("criticism"),
                "response_status": status,
                "review_status": status,
                "advisor_strategy": advisor_strategy,
                "used_evidence_ids": item.get("evidence_ids") or [],
                "used_artifact_ids": artifact_ids,
                "researcher_memo_used": bool(researcher_memos),
                "artifact_interpretation_used": bool(researcher_memos),
                "metric_sanity_flags": metric_flags,
                "claim_boundary": boundary,
                "missing_artifacts": [] if artifact_ok else list(item.get("blocked_until") or []),
                "student_rebuttal": rebuttal,
                "planned_revision": clean_text(item.get("safe_response_boundary"))
                or "Revise the manuscript to make the evidence boundary explicit.",
                "limitations_to_acknowledge": ""
                if status == REVIEW_STATUS_USABLE
                else "The current manuscript should not imply stronger evidence than is available.",
                "no_new_results_claimed": True,
                "blocked_reason": blocked_reason,
                "advisor_attention_needed": status in {REVIEW_STATUS_BLOCKED, REVIEW_STATUS_NEEDS_HUMAN},
            }
        )
    return {
        "version": REBUTTAL_DRAFT_VERSION,
        "created_at": iso_now(),
        "critique_pack_version": critique_pack.get("version"),
        "responses": responses,
        "claim_boundary_map": claim_boundary_map,
    }


def student_defense_markdown(rebuttal: dict[str, Any]) -> str:
    lines = ["# Student Defense Draft", ""]
    for item in rebuttal.get("responses", []):
        lines.extend(
            [
                f"## {item.get('critique_id')}: {item.get('response_status')}",
                f"- Missing artifacts: {', '.join(item.get('missing_artifacts') or ['none'])}",
                f"- Metric sanity flags: {', '.join(item.get('metric_sanity_flags') or ['none'])}",
                f"- Advisor strategy: {item.get('advisor_strategy')}",
                f"- No new results claimed: {item.get('no_new_results_claimed')}",
                "",
                item.get("student_rebuttal") or "",
                "",
                f"Planned revision: {item.get('planned_revision')}",
                "",
            ]
        )
    return "\n".join(lines)


def write_student_defense_files(workspace: Path, rebuttal: dict[str, Any]) -> dict[str, str]:
    paths = {
        "rebuttal_draft": workspace / "rebuttal_draft.json",
        "student_defense": workspace / "student_defense.md",
        "claim_boundary_map": workspace / "claim_boundary_map.json",
    }
    write_json(paths["rebuttal_draft"], rebuttal)
    write_json(paths["claim_boundary_map"], {"version": "claim_boundary_map_v1", "items": rebuttal.get("claim_boundary_map", [])})
    paths["student_defense"].write_text(student_defense_markdown(rebuttal), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def validate_rebuttal_integrity(rebuttal: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for item in rebuttal.get("responses", []):
        cid = item.get("critique_id")
        if item.get("no_new_results_claimed") is False:
            issues.append(f"{cid}: no_new_results_claimed=false")
        if item.get("response_status") == REVIEW_STATUS_BLOCKED:
            text = clean_text(item.get("student_rebuttal")).lower()
            for phrase in BANNED_BLOCKED_PHRASES:
                if phrase in text:
                    issues.append(f"{cid}: blocked response contains banned phrase '{phrase}'")
    return issues


def advisor_tone(status: str) -> str:
    return {
        REVIEW_STATUS_USABLE: "researcher-led draft support",
        REVIEW_STATUS_NEEDS_HUMAN: "human judgment required",
        REVIEW_STATUS_BLOCKED: "action item only",
    }.get(status, "bounded defense")


def build_advisor_shield(rebuttal: dict[str, Any], *, stop_on_blocked: bool = True) -> dict[str, Any]:
    issues = validate_rebuttal_integrity(rebuttal)
    responses = rebuttal.get("responses", [])
    blocked = [item for item in responses if item.get("response_status") == REVIEW_STATUS_BLOCKED]
    needs_human = [item for item in responses if item.get("response_status") == REVIEW_STATUS_NEEDS_HUMAN]
    final_items: list[dict[str, Any]] = []
    action_items: list[dict[str, Any]] = []
    for item in responses:
        status = item.get("response_status")
        strategy = item.get("advisor_strategy") or "narrow"
        if status == REVIEW_STATUS_BLOCKED:
            final = ""
            action_items.append(
                {
                    "critique_id": item.get("critique_id"),
                    "reason": item.get("blocked_reason") or "Missing evidence or researcher memo.",
                    "needed": item.get("missing_artifacts") or ["researcher_memo"],
                    "recommended_action": "Provide artifact/researcher memo or concede this point as a limitation.",
                }
            )
        elif status == REVIEW_STATUS_NEEDS_HUMAN:
            final = (
                "We appreciate the comment. This point is useful for revision, but it requires researcher judgment before "
                "we make a strong technical defense. We will narrow the manuscript language and explicitly preserve the "
                "remaining limitation."
            )
            action_items.append(
                {
                    "critique_id": item.get("critique_id"),
                    "reason": "Metric sanity flags or interpretation limits require researcher judgment.",
                    "needed": item.get("metric_sanity_flags") or ["researcher_judgment"],
                    "recommended_action": "Review the memo and decide whether to defend, narrow, or concede.",
                }
            )
        elif strategy == "narrow":
            final = (
                "We agree that the claim should be stated more precisely. We will narrow the wording so that the contribution "
                "matches the evidence presented in the manuscript."
            )
        elif strategy == "concede":
            final = (
                "We appreciate the reviewer's point and will revise the manuscript to acknowledge this limitation rather than "
                "overstate the current evidence."
            )
        else:
            final = (
                "We appreciate the comment. We have clarified the relevant manuscript text and tied the response directly to "
                "the available evidence, while keeping the claim within the stated boundary."
            )
        final_items.append(
            {
                "critique_id": item.get("critique_id"),
                "response_status": status,
                "advisor_strategy": strategy,
                "tone_mode": advisor_tone(str(status)),
                "included_in_rebuttal": status != REVIEW_STATUS_BLOCKED,
                "final_response": final,
                "planned_revision": item.get("planned_revision"),
                "claim_boundary": item.get("claim_boundary"),
            }
        )
    ready = not issues and not blocked and not needs_human
    return {
        "version": ADVISOR_SHIELD_VERSION,
        "created_at": iso_now(),
        "ready_for_researcher_review": ready,
        "integrity_issues": issues,
        "blocked_count": len(blocked),
        "needs_human_judgment_count": len(needs_human),
        "researcher_action_items": action_items,
        "responses": final_items,
    }


def advisor_markdown(advisor: dict[str, Any]) -> str:
    lines = [
        "# Final Rebuttal Letter Draft",
        "",
        f"ready_for_researcher_review={str(advisor.get('ready_for_researcher_review')).lower()}",
        f"blocked_count={advisor.get('blocked_count')}",
        f"needs_human_judgment_count={advisor.get('needs_human_judgment_count')}",
        "",
    ]
    if advisor.get("integrity_issues"):
        lines.extend(["## Integrity Warnings", ""])
        for issue in advisor.get("integrity_issues", []):
            lines.append(f"- {issue}")
        lines.append("")
    included = [item for item in advisor.get("responses", []) if item.get("included_in_rebuttal")]
    for item in included:
        lines.extend(
            [
                f"## Response to {item.get('critique_id')}",
                f"- Status: {item.get('response_status')}",
                f"- Advisor strategy: {item.get('advisor_strategy')}",
                f"- Tone mode: {item.get('tone_mode')}",
                "",
                item.get("final_response") or "",
                "",
            ]
        )
    if advisor.get("researcher_action_items"):
        lines.extend(["## Researcher Action Items", ""])
        for item in advisor.get("researcher_action_items", []):
            needed = ", ".join(item.get("needed") or ["researcher_judgment"])
            lines.append(f"- {item.get('critique_id')}: {item.get('recommended_action')} Needed: {needed}.")
        lines.append("")
    return "\n".join(lines)


def revision_plan_markdown(advisor: dict[str, Any]) -> str:
    lines = ["# Revision Plan", ""]
    for item in advisor.get("responses", []):
        lines.extend(
            [
                f"## {item.get('critique_id')}",
                f"- Status: {item.get('response_status')}",
                f"- Advisor strategy: {item.get('advisor_strategy')}",
                f"- Action: {item.get('planned_revision') or 'Revise according to final response boundary.'}",
                "",
            ]
        )
    return "\n".join(lines)


def write_advisor_files(workspace: Path, advisor: dict[str, Any]) -> dict[str, str]:
    paths = {
        "advisor_shield": workspace / "advisor_shield.json",
        "final_rebuttal_letter": workspace / "final_rebuttal_letter.md",
        "revision_plan": workspace / "revision_plan.md",
    }
    write_json(paths["advisor_shield"], advisor)
    paths["final_rebuttal_letter"].write_text(advisor_markdown(advisor), encoding="utf-8")
    paths["revision_plan"].write_text(revision_plan_markdown(advisor), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}

