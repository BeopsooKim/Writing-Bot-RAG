#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def resolve_defense_root(path: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    if (candidate / "src" / "research_rag").is_dir():
        return candidate
    nested = candidate / "Defense_RAG"
    if (nested / "src" / "research_rag").is_dir():
        return nested
    raise SystemExit(f"Defense_RAG src not found under {candidate}")


def load_defense_modules(defense_root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(defense_root / "src"))
    from research_rag.committee_answer_card import build_answer_card, write_answer_card
    from research_rag.defense_claim_ledger import load_claim_ledger
    from research_rag.evidence_status_loader import classify_evidence_item, load_evidence_status
    from research_rag.forbidden_claims import rewrite_to_safe_boundary
    from research_rag.script_status import load_script_status

    return {
        "build_answer_card": build_answer_card,
        "write_answer_card": write_answer_card,
        "load_claim_ledger": load_claim_ledger,
        "classify_evidence_item": classify_evidence_item,
        "load_evidence_status": load_evidence_status,
        "rewrite_to_safe_boundary": rewrite_to_safe_boundary,
        "load_script_status": load_script_status,
    }


def build_card(args: argparse.Namespace) -> dict[str, Any]:
    defense_root = resolve_defense_root(args.defense_root)
    mods = load_defense_modules(defense_root)
    script_status = mods["load_script_status"](defense_root)
    evidence_lock = mods["load_evidence_status"](defense_root)
    evidence_decision = mods["classify_evidence_item"](args.question, evidence_lock, "final_claim_support")
    safe = mods["rewrite_to_safe_boundary"](args.question)
    rows = mods["load_claim_ledger"](Path(args.ledger)) if args.ledger and Path(args.ledger).exists() else []
    card = mods["build_answer_card"](
        args.question,
        rows,
        {
            "root": str(defense_root),
            "intended_use": args.intended_use,
            "safe_boundary_note": safe.safe_boundary_note,
            "prepared_answer": args.prepared_answer,
            "blocked_items_as_actions": [evidence_decision.reason] if evidence_decision.status == "blocked" else [],
        },
    )
    if args.output:
        mods["write_answer_card"](card, Path(args.output))
    return {
        "gate": card["answer_gate"],
        "script_status_checked": True,
        "script_status": script_status.status,
        "final_prose_allowed": card["final_prose_allowed"],
        "prepared_answer": card["prepared_answer"],
        "prepared_answer_allowed": card["prepared_answer_allowed"],
        "raw_chunks_allowed": False,
        "evidence_decision": evidence_decision.__dict__,
        "answer_card": card,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a committee answer card from Defense_RAG ledger rows.")
    parser.add_argument("--defense-root", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--output")
    parser.add_argument("--intended-use", default="polished_committee_answer")
    parser.add_argument("--prepared-answer")
    args = parser.parse_args()
    print(json.dumps(build_card(args), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
