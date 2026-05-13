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
    parser = argparse.ArgumentParser(description="Create a humble-but-firm advisor shield from a rebuttal draft.")
    parser.add_argument("--workspace", required=True, help="Workspace path, workspace id fragment, or 'latest'.")
    parser.add_argument("--rebuttal", default=None, help="Defaults to <workspace>/rebuttal_draft.json.")
    parser.add_argument("--stop-on-blocked", choices=["true", "false"], default="true")
    parser.add_argument("--root", default=default_root())
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    bootstrap(root)

    from research_rag.bot_workflow import resolve_workspace
    from research_rag.reviewer_hell import build_advisor_shield, read_json, write_advisor_files

    workspace = resolve_workspace(root, args.workspace)
    rebuttal_path = Path(args.rebuttal) if args.rebuttal else workspace / "rebuttal_draft.json"
    if not rebuttal_path.exists():
        raise SystemExit(f"No rebuttal draft found: {rebuttal_path}")

    rebuttal = read_json(rebuttal_path)
    advisor = build_advisor_shield(rebuttal, stop_on_blocked=args.stop_on_blocked == "true")
    files = write_advisor_files(workspace, advisor)
    status = "ready_for_researcher_review" if advisor.get("ready_for_researcher_review") else "needs_researcher_review"
    if advisor.get("integrity_issues"):
        status = "integrity_blocked"
    print(
        json.dumps(
            {
                "status": status,
                "workspace": str(workspace),
                "ready_for_researcher_review": advisor.get("ready_for_researcher_review"),
                "blocked_count": advisor.get("blocked_count"),
                "needs_human_judgment_count": advisor.get("needs_human_judgment_count"),
                "researcher_action_items": advisor.get("researcher_action_items"),
                "integrity_issues": advisor.get("integrity_issues"),
                "files": files,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


