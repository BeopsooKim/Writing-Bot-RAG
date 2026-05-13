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
    parser = argparse.ArgumentParser(description="Create a student defense draft from a Reviewer Hell critique pack.")
    parser.add_argument("--workspace", required=True, help="Workspace path, workspace id fragment, or 'latest'.")
    parser.add_argument("--critique", default=None, help="Defaults to <workspace>/critique_pack.json.")
    parser.add_argument("--artifacts", default=None, help="Defaults to <workspace>/human_artifacts.json if present.")
    parser.add_argument("--root", default=default_root())
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    bootstrap(root)

    from research_rag.bot_workflow import resolve_workspace
    from research_rag.reviewer_hell import build_student_defense, load_artifacts, read_json, write_student_defense_files

    workspace = resolve_workspace(root, args.workspace)
    critique_path = Path(args.critique) if args.critique else workspace / "critique_pack.json"
    if not critique_path.exists():
        raise SystemExit(f"No critique pack found: {critique_path}")
    artifacts_path = Path(args.artifacts) if args.artifacts else workspace / "human_artifacts.json"

    critique_pack = read_json(critique_path)
    artifacts = read_json(artifacts_path) if artifacts_path.exists() else load_artifacts(workspace)
    rebuttal = build_student_defense(critique_pack, artifacts)
    files = write_student_defense_files(workspace, rebuttal)
    blocked = [item for item in rebuttal.get("responses", []) if item.get("response_status") == "blocked"]
    needs_human = [item for item in rebuttal.get("responses", []) if item.get("response_status") == "needs_human_judgment"]
    usable = [item for item in rebuttal.get("responses", []) if item.get("response_status") == "usable_for_draft"]
    if blocked:
        status = "blocked"
    elif needs_human:
        status = "needs_human_judgment"
    else:
        status = "usable_for_draft"
    print(
        json.dumps(
            {
                "status": status,
                "workspace": str(workspace),
                "responses": len(rebuttal.get("responses", [])),
                "blocked": len(blocked),
                "needs_human_judgment": len(needs_human),
                "usable_for_draft": len(usable),
                "files": files,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


