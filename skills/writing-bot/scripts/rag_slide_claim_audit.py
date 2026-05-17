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
    from research_rag.defense_claim_ledger import load_claim_ledger
    from research_rag.evidence_status_loader import load_evidence_status
    from research_rag.forbidden_claims import detect_forbidden_claim
    from research_rag.script_status import load_script_status
    from research_rag.slide_audit import build_slide_audit_report, extract_slide_claims

    return {
        "load_claim_ledger": load_claim_ledger,
        "load_evidence_status": load_evidence_status,
        "detect_forbidden_claim": detect_forbidden_claim,
        "load_script_status": load_script_status,
        "build_slide_audit_report": build_slide_audit_report,
        "extract_slide_claims": extract_slide_claims,
    }


def audit_slides(args: argparse.Namespace) -> dict[str, Any]:
    defense_root = resolve_defense_root(args.defense_root)
    mods = load_defense_modules(defense_root)
    script_status = mods["load_script_status"](defense_root)
    mods["load_evidence_status"](defense_root)
    claims = mods["extract_slide_claims"](Path(args.slides))
    rows = mods["load_claim_ledger"](Path(args.ledger)) if args.ledger and Path(args.ledger).exists() else []
    report = mods["build_slide_audit_report"](claims, rows, root=defense_root)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "gate": "blocked" if any(card["gate"] == "blocked" for card in report["slide_audit_cards"]) else "needs_human_judgment",
        "script_status_checked": True,
        "script_status": script_status.status,
        "final_prose_allowed": False,
        "prepared_answer": None,
        "raw_chunks_allowed": False,
        "slide_audit_report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit slide claims through Defense_RAG ledger and slide gates.")
    parser.add_argument("--defense-root", required=True)
    parser.add_argument("--slides", required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--output")
    args = parser.parse_args()
    print(json.dumps(audit_slides(args), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
