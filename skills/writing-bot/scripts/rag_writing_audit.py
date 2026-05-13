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
    parser = argparse.ArgumentParser(description="Audit claims against a RAG evidence-pack workspace.")
    parser.add_argument("--workspace", required=True, help="Workspace path, workspace id fragment, or 'latest'.")
    parser.add_argument("--claim", default=None)
    parser.add_argument("--text-file", default=None)
    parser.add_argument("--root", default=default_root())
    args = parser.parse_args()

    if not args.claim and not args.text_file:
        raise SystemExit("Provide --claim or --text-file.")

    root = Path(args.root).expanduser().resolve()
    bootstrap(root)

    from research_rag.bot_workflow import audit_claims, resolve_workspace, split_claims, write_audit_files

    workspace = resolve_workspace(root, args.workspace)
    pack_path = workspace / "evidence_pack.json"
    if not pack_path.exists():
        raise SystemExit(f"No evidence_pack.json found in {workspace}")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    else:
        text = str(args.claim)
    claims = split_claims(text)
    audit = audit_claims(pack, claims)
    files = write_audit_files(workspace, audit)
    print(
        json.dumps(
            {
                "status": "ok",
                "workspace": str(workspace),
                "claims": len(claims),
                "warnings": audit.get("warnings", []),
                "files": files,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


