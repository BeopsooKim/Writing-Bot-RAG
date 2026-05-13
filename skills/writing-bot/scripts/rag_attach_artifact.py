from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def default_root() -> str:
    env_root = os.environ.get("RESEARCH_RAG_ROOT")
    if env_root:
        return env_root
    windows_root = Path(r"D:\Research_RAG")
    if windows_root.exists():
        return str(windows_root)
    return str(Path.home() / ".research_rag")


def bootstrap(root: Path) -> None:
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach a researcher memo and artifact to a Reviewer Hell workspace.")
    parser.add_argument("--workspace", required=True, help="Workspace path, workspace id fragment, or 'latest'.")
    parser.add_argument("--critique-id", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument(
        "--artifact-type",
        required=True,
        choices=["simulation_result", "figure", "table", "code_commit", "methods_note", "advisor_decision"],
    )
    parser.add_argument("--researcher-memo", default=None)
    parser.add_argument("--human-interpretation", default=None, help="Deprecated alias for --researcher-memo.")
    parser.add_argument("--interpretation-limits", default=None)
    parser.add_argument("--supports-claim", default=None)
    parser.add_argument("--metric", choices=["settling_time", "overshoot", "steady_state_error", "custom"], default=None)
    parser.add_argument("--metric-value", type=float, default=None)
    parser.add_argument("--unit", default=None)
    parser.add_argument("--baseline-value", type=float, default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--threshold-source", choices=["standard", "paper", "advisor", "user"], default=None)
    parser.add_argument("--threshold-direction", choices=["lt", "le", "gt", "ge"], default=None)
    parser.add_argument("--allow-uninterpreted", action="store_true")
    parser.add_argument("--root", default=default_root())
    args = parser.parse_args()

    researcher_memo = args.researcher_memo or args.human_interpretation
    if not researcher_memo and not args.allow_uninterpreted:
        raise SystemExit("Provide --researcher-memo, or pass --allow-uninterpreted to record a non-supporting artifact.")

    root = Path(args.root).expanduser().resolve()
    bootstrap(root)

    from research_rag.bot_workflow import resolve_workspace
    from research_rag.reviewer_hell import attach_artifact

    workspace = resolve_workspace(root, args.workspace)
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        raise SystemExit(f"Artifact file not found: {file_path}")

    record = attach_artifact(
        workspace,
        critique_id=args.critique_id,
        file_path=file_path,
        artifact_type=args.artifact_type,
        researcher_memo=researcher_memo,
        human_interpretation=args.human_interpretation,
        interpretation_limits=args.interpretation_limits,
        supports_claim=args.supports_claim,
        metric=args.metric,
        metric_value=args.metric_value,
        unit=args.unit,
        baseline_value=args.baseline_value,
        threshold=args.threshold,
        threshold_source=args.threshold_source,
        threshold_direction=args.threshold_direction,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "workspace": str(workspace),
                "review_status": record.get("review_status"),
                "metric_sanity_flags": (record.get("metric_summary") or {}).get("sanity_flags", []),
                "artifact": record,
                "files": {
                    "human_artifacts": str(workspace / "human_artifacts.json"),
                    "evidence_pack_augmented": str(workspace / "evidence_pack_augmented.json"),
                },
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


