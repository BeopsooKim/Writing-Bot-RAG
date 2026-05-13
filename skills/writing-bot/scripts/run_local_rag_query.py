from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def find_python(root: Path) -> Path:
    candidates = [
        root / ".conda" / "python.exe",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def default_root() -> str:
    env_root = os.environ.get("RESEARCH_RAG_ROOT")
    if env_root:
        return env_root
    windows_root = Path(r"D:\Research_RAG")
    if windows_root.exists():
        return str(windows_root)
    return str(Path.home() / ".research_rag")


def run_once(cmd: list[str], env: dict[str, str], timeout_s: float) -> int:
    result = subprocess.run(
        cmd,
        env=env,
        timeout=timeout_s,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def unavailable_payload(query: str, status: str, message: str, **extra: object) -> str:
    payload = {
        "status": status,
        "message": "local RAG evidence unavailable",
        "detail": message,
        "query": query,
        "results": [],
    }
    payload.update(extra)
    return json.dumps(payload, indent=2, ensure_ascii=True)


def build_query_cmd(args: argparse.Namespace, python: Path, root: Path, *, force_no_rerank: bool = False) -> list[str]:
    mode = "hybrid" if force_no_rerank else args.mode
    cmd = [
        str(python),
        "-m",
        "research_rag.cli",
        "--root",
        str(root),
        "query",
        args.query,
        "--profile",
        args.profile,
        "--top-k",
        str(args.top_k),
        "--mode",
        mode,
        "--candidate-k",
        str(args.candidate_k),
        "--rrf-k",
        str(args.rrf_k),
        "--weight-bm25",
        str(args.weight_bm25),
        "--weight-dense",
        str(args.weight_dense),
        "--reranker-model",
        args.reranker_model,
        "--reranker-fallback-model",
        args.reranker_fallback_model,
        "--rerank-candidate-k",
        str(args.rerank_candidate_k),
        "--rerank-batch-size",
        str(args.rerank_batch_size),
    ]
    if args.dense and not force_no_rerank:
        cmd.append("--dense")
    if args.model:
        cmd.extend(["--model", args.model])
    if args.rerank and not force_no_rerank:
        cmd.append("--rerank")
        cmd.extend(["--rerank-time-budget-s", str(args.deadline_s)])
    else:
        cmd.append("--no-rerank")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local RAG query for writing evidence.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--profile", default="research")
    parser.add_argument("--root", default=default_root())
    parser.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="hybrid")
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--weight-bm25", type=float, default=1.2)
    parser.add_argument("--weight-dense", type=float, default=1.0)
    parser.add_argument("--dense", action="store_true", help="Alias for --mode dense.")
    parser.add_argument("--model", default=None, help="Dense embedding model override.")
    rerank_group = parser.add_mutually_exclusive_group()
    rerank_group.add_argument("--rerank", dest="rerank", action="store_true")
    rerank_group.add_argument("--no-rerank", dest="rerank", action="store_false")
    parser.set_defaults(rerank=True)
    parser.add_argument("--reranker-model", default="BAAI/bge-reranker-large")
    parser.add_argument("--reranker-fallback-model", default="BAAI/bge-reranker-base")
    parser.add_argument("--rerank-candidate-k", type=int, default=30)
    parser.add_argument("--rerank-batch-size", type=int, default=4)
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--deadline-s", type=float, default=50.0)
    args = parser.parse_args()

    root = Path(args.root)
    python = find_python(root)
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("HF_HOME", str(root / "models" / "huggingface"))
    env.setdefault("SENTENCE_TRANSFORMERS_HOME", str(root / "models" / "huggingface" / "sentence-transformers"))
    env.setdefault("RESEARCH_RAG_LOCAL_FILES_ONLY", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_VERBOSITY", "error")
    src = root / "src"
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = build_query_cmd(args, python, root)
    try:
        return run_once(cmd, env, args.timeout_s)
    except subprocess.TimeoutExpired:
        sys.stderr.write("Local RAG rerank timed out; retrying with hybrid retrieval and no rerank.\n")

    retry_cmd = build_query_cmd(args, python, root, force_no_rerank=True)
    try:
        retry_code = run_once(retry_cmd, env, min(max(args.timeout_s, 20.0), 60.0))
    except subprocess.TimeoutExpired as retry_exc:
        print(
            unavailable_payload(
                args.query,
                "timeout",
                str(retry_exc),
                rerank_status="timeout",
                fallback_attempted=True,
            )
        )
        return 0
    if retry_code != 0:
        print(
            unavailable_payload(
                args.query,
                "error",
                f"fallback query failed with exit code {retry_code}",
                rerank_status="fallback_failed",
                fallback_attempted=True,
            )
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


