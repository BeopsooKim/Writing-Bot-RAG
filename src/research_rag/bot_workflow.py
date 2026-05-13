from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RagConfig
from .db import connect


PACK_VERSION = "evidence_pack_v1"
DEFAULT_PARENT_TIMEOUT_S = 30.0
TIMEOUT_RESERVE_S = 5.0
METADATA_ONLY_WARNING = (
    "**경고: 이 결과는 metadata-only입니다. 본문 PDF/page를 확인할 수 없어 "
    "이 claim의 인용 근거로 보장할 수 없습니다.**"
)
UNRERANKED_WARNING = (
    "**주의: reranker 검증이 완료되지 않은 후보 근거입니다. "
    "claim support 여부를 별도로 확인해야 합니다.**"
)


@dataclass(frozen=True)
class ProfileSpec:
    profile: str
    mode: str
    top_k: int
    candidate_k: int
    rerank: bool
    timeout_s: float
    rerank_time_budget_s: float | None = None


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^A-Za-z0-9가-힣]+", "_", text.strip()).strip("_").lower()
    return (slug[:max_len].strip("_") or "research_brief")


def parent_timeout(default: float = DEFAULT_PARENT_TIMEOUT_S) -> float:
    raw = os.environ.get("RAG_PARENT_TIMEOUT_S")
    if not raw:
        return default
    try:
        return max(8.0, float(raw))
    except ValueError:
        return default


def safe_child_timeout(parent_timeout_s: float, requested_s: float) -> float:
    return max(3.0, min(requested_s, parent_timeout_s - TIMEOUT_RESERVE_S))


def infer_profile(topic: str, parent_timeout_s: float) -> tuple[str, list[str], str]:
    q = topic.lower()
    warnings: list[str] = []
    exact_patterns = [
        r"\b10\.\d{4,9}/\S+",
        r"\barxiv[:\s]\d{4}\.\d{4,5}",
        r"\bieee\s+(?:std\.?\s*)?\d+",
        r"\biec\s+\d+",
        r"\bks\s+c\s+iec\s+\d+",
    ]
    if any(re.search(pattern, q, re.IGNORECASE) for pattern in exact_patterns):
        return "fast", warnings, "exact_lookup"
    if re.search(r"\b(citation|claim|support|reviewer|rebuttal|integrity|source)\b|인용|주장|근거|리뷰어", q):
        if parent_timeout_s >= 65:
            return "deep", warnings, "citation_critical"
        warnings.append("deep_downgraded_parent_timeout")
        return "balanced", warnings, "citation_critical"
    if re.search(r"\b(gap|literature|trend|planning|method|methodology|positioning|survey|review)\b|문헌|동향|갭|방법론", q):
        return "balanced", warnings, "conceptual_research"
    return "balanced", warnings, "conceptual_research"


def resolve_profile(requested: str, topic: str, parent_timeout_s: float) -> tuple[ProfileSpec, list[str], str]:
    warnings: list[str] = []
    intent = "user_specified"
    profile = requested
    if requested == "auto":
        profile, warnings, intent = infer_profile(topic, parent_timeout_s)
    elif requested == "deep" and parent_timeout_s < 65:
        profile = "balanced"
        warnings.append("deep_downgraded_parent_timeout")
        intent = "citation_critical"

    if profile == "fast":
        return ProfileSpec("fast", "bm25", 6, 6, False, safe_child_timeout(parent_timeout_s, 8.0)), warnings, intent
    if profile == "deep":
        return (
            ProfileSpec("deep", "hybrid", 8, 50, True, safe_child_timeout(parent_timeout_s, 60.0), 45.0),
            warnings,
            intent,
        )
    return ProfileSpec("balanced", "hybrid", 6, 30, False, safe_child_timeout(parent_timeout_s, 20.0)), warnings, intent


def find_python(root: Path) -> Path:
    for candidate in [root / ".conda" / "python.exe", root / ".venv" / "Scripts" / "python.exe", root / "venv" / "Scripts" / "python.exe"]:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def rag_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("HF_HOME", str(root / "models" / "huggingface"))
    env.setdefault("SENTENCE_TRANSFORMERS_HOME", str(root / "models" / "huggingface" / "sentence-transformers"))
    env.setdefault("RESEARCH_RAG_LOCAL_FILES_ONLY", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_VERBOSITY", "error")
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stdout[start : end + 1])
        raise


def meaningful_stderr(stderr: str) -> str:
    ignored = (
        "Loading weights:",
        "The Transformer `cache_dir` argument is deprecated",
        "cache_dir argument is deprecated",
    )
    lines = []
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in ignored):
            continue
        if re.fullmatch(r"\d+%.*", stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def run_query(root: Path, topic: str, spec: ProfileSpec) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    cmd = [
        str(find_python(root)),
        "-m",
        "research_rag.cli",
        "--root",
        str(root),
        "query",
        topic,
        "--top-k",
        str(spec.top_k),
        "--mode",
        spec.mode,
        "--candidate-k",
        str(spec.candidate_k),
    ]
    cmd.append("--rerank" if spec.rerank else "--no-rerank")
    if spec.rerank and spec.rerank_time_budget_s:
        cmd.extend(["--rerank-time-budget-s", str(spec.rerank_time_budget_s)])

    try:
        result = subprocess.run(
            cmd,
            env=rag_env(root),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=spec.timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return [], [f"{spec.profile}_query_timeout"], {"timeout": str(exc), "cmd": cmd}

    if result.returncode != 0:
        return [], [f"{spec.profile}_query_failed"], {"stderr": result.stderr[-1200:], "cmd": cmd}
    payload = parse_json_stdout(result.stdout)
    warnings: list[str] = []
    stderr = meaningful_stderr(result.stderr)
    if stderr:
        warnings.append("rag_query_stderr_present")
    return list(payload.get("results") or []), warnings, {"report": payload.get("report"), "stderr": stderr[-1200:]}


def cleanup_workspaces(workspaces_root: Path, *, mode: str, keep: int = 50, max_age_days: int = 30) -> dict[str, Any]:
    if mode == "never":
        return {"cleanup": "skipped"}
    workspaces_root.mkdir(parents=True, exist_ok=True)
    root_resolved = workspaces_root.resolve()
    now_ts = datetime.now().timestamp()
    removed: list[str] = []

    candidates = [path for path in workspaces_root.iterdir() if path.is_dir() and not (path / ".keep").exists()]
    candidates.sort(key=lambda path: path.stat().st_mtime)

    for path in list(candidates):
        age_days = (now_ts - path.stat().st_mtime) / 86400
        if mode in {"auto", "prune"} and age_days > max_age_days:
            resolved = path.resolve()
            if root_resolved in resolved.parents:
                shutil.rmtree(resolved)
                removed.append(path.name)

    remaining = [path for path in workspaces_root.iterdir() if path.is_dir() and not (path / ".keep").exists()]
    remaining.sort(key=lambda path: path.stat().st_mtime)
    while len(remaining) > keep:
        path = remaining.pop(0)
        resolved = path.resolve()
        if root_resolved in resolved.parents:
            shutil.rmtree(resolved)
            removed.append(path.name)

    return {"cleanup": mode, "removed": removed, "keep": keep, "max_age_days": max_age_days}


def create_workspace(root: Path, topic: str, workspace: str, *, cleanup: str, keep: int, max_age_days: int) -> tuple[Path, dict[str, Any]]:
    workspaces_root = root / "workspaces"
    cleanup_result = cleanup_workspaces(workspaces_root, mode=cleanup, keep=keep, max_age_days=max_age_days)
    if workspace and workspace != "auto":
        path = Path(workspace)
        if not path.is_absolute():
            path = workspaces_root / workspace
    else:
        path = workspaces_root / f"{utc_stamp()}_{slugify(topic)}"
    path.mkdir(parents=True, exist_ok=True)
    return path, cleanup_result


def _load_chunk_texts(config: RagConfig, chunk_ids: list[int]) -> dict[int, str]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    with connect(config.db_path) as conn:
        rows = conn.execute(f"SELECT id, text FROM chunks WHERE id IN ({placeholders})", chunk_ids).fetchall()
    return {int(row["id"]): str(row["text"] or "") for row in rows}


def clean_snippet(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    return cleaned[: max(0, limit - 3)].rstrip() + "..." if len(cleaned) > limit else cleaned


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", normalized)
    return [part.strip() for part in parts if part.strip()]


def structural_excerpt(row: dict[str, Any], chunk_text: str, limit: int = 400) -> tuple[str, str, list[str]]:
    warnings = ["semantic_excerpt_not_localized"]
    lines = [line.strip() for line in chunk_text.splitlines() if line.strip()]
    structural = [line for line in lines if re.search(r"^(?:Table|TABLE|Fig\.?|Figure)\s+|\bEq\.?\s*\(?\d+\)?|[=≤≥∑∫]", line)]
    prefix = f"{row.get('section')}: " if row.get("section") else ""
    if structural:
        excerpt = prefix + " ".join(structural[:2])
        return clean_snippet(excerpt, limit), "dense_structural_excerpt", warnings
    sentences = split_sentences(chunk_text)
    if not sentences:
        return clean_snippet(prefix + chunk_text, limit), "dense_structural_excerpt", warnings
    if len(sentences) == 1:
        return clean_snippet(prefix + sentences[0], limit), "dense_structural_excerpt", warnings
    return clean_snippet(prefix + sentences[0] + " ... " + sentences[-1], limit), "dense_structural_excerpt", warnings


def key_sentence(row: dict[str, Any], chunk_text: str) -> tuple[str, str, list[str]]:
    if row.get("bm25_rank") or row.get("retrieval") == "bm25":
        snippet = clean_snippet(row.get("snippet"), 400)
        if snippet:
            return snippet, "fts_snippet", []
    return structural_excerpt(row, chunk_text)


def evidence_status(row: dict[str, Any], warning_flags: list[str]) -> tuple[str, str]:
    if row.get("source_quality") == "metadata-only":
        return "Discovery lead only; source body/page is unavailable.", "Find and inspect the source PDF before citing."
    if "semantic_excerpt_not_localized" in warning_flags:
        return "Semantic candidate only; inspect the full PDF/page before using.", "Dense-only excerpt is not localized to an exact lexical hit."
    if row.get("source_quality") == "verified":
        return "Page-grounded candidate evidence; exact claim support still requires audit.", "Check whether the retrieved page directly supports the claim boundary."
    return "Candidate evidence only.", "Source/page support remains incomplete."


def build_evidence_pack(
    config: RagConfig,
    *,
    topic: str,
    requested_profile: str,
    spec: ProfileSpec,
    intent: str,
    rows: list[dict[str, Any]],
    warnings: list[str],
    workspace: Path,
) -> dict[str, Any]:
    chunk_ids = [int(row["chunk_id"]) for row in rows if row.get("chunk_id") is not None]
    chunk_texts = _load_chunk_texts(config, chunk_ids)
    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        flags: list[str] = []
        if row.get("source_quality") == "metadata-only":
            flags.append("metadata_only")
        if row.get("rerank_status") in {"disabled", "partial_timeout", "fallback_to_rrf"}:
            flags.append(f"rerank_{row.get('rerank_status')}")
        sentence, method, method_warnings = key_sentence(row, chunk_texts.get(int(row.get("chunk_id") or 0), ""))
        flags.extend(method_warnings)
        supported_boundary, evidence_gap = evidence_status(row, flags)
        items.append(
            {
                "evidence_id": f"E{idx:02d}",
                "work_id": row.get("work_id"),
                "chunk_id": row.get("chunk_id"),
                "title": row.get("title"),
                "year": row.get("year"),
                "source_quality": row.get("source_quality"),
                "relevance_label": row.get("relevance_label"),
                "rerank_status": row.get("rerank_status"),
                "page_range": row.get("page_range"),
                "local_path": row.get("local_path"),
                "key_sentence": sentence,
                "key_sentence_method": method,
                "supported_boundary": supported_boundary,
                "evidence_gap": evidence_gap,
                "warning_flags": sorted(set(flags)),
            }
        )
    pack_id = workspace.name
    return {
        "version": PACK_VERSION,
        "pack_id": pack_id,
        "topic": topic,
        "requested_profile": requested_profile,
        "profile": spec.profile,
        "intent": intent,
        "created_at": iso_now(),
        "query_set": [topic],
        "items": items,
        "warnings": sorted(set(warnings)),
        "workspace": str(workspace),
    }


def markdown_brief(pack: dict[str, Any]) -> str:
    lines = [
        f"# Research Brief: {pack['topic']}",
        "",
        f"- Profile: {pack['profile']} (requested: {pack['requested_profile']})",
        f"- Intent: {pack['intent']}",
        f"- Warnings: {', '.join(pack.get('warnings') or ['none'])}",
        "",
        "## Evidence",
    ]
    for item in pack.get("items", []):
        warnings = ", ".join(item.get("warning_flags") or ["none"])
        lines.extend(
            [
                f"### {item['evidence_id']}. {item.get('title')}",
                f"- Page: {item.get('page_range') or 'n/a'}",
                f"- Quality: {item.get('source_quality')} / {item.get('relevance_label')} / rerank={item.get('rerank_status')}",
                f"- Key sentence: {item.get('key_sentence') or ''}",
                f"- Supported boundary: {item.get('supported_boundary')}",
                f"- Evidence gap: {item.get('evidence_gap')}",
                f"- Warning flags: {warnings}",
                "",
            ]
        )
    return "\n".join(lines)


def handoff_markdown(pack: dict[str, Any]) -> str:
    usable = [item for item in pack.get("items", []) if item.get("source_quality") != "metadata-only"]
    discovery = [item for item in pack.get("items", []) if item.get("source_quality") == "metadata-only"]
    lines = [f"# Handoff: {pack['topic']}", "", "## Usable evidence"]
    for item in usable:
        lines.append(f"- {item['evidence_id']}: {item.get('title')} ({item.get('page_range') or 'n/a'})")
    lines.extend(["", "## Discovery-only leads"])
    for item in discovery:
        lines.append(f"- {item['evidence_id']}: {item.get('title')}")
    lines.extend(["", "## Evidence gaps"])
    for item in pack.get("items", []):
        lines.append(f"- {item['evidence_id']}: {item.get('evidence_gap')}")
    lines.extend(["", "## Suggested writing task", "Use this pack to build a claim-evidence map before drafting or revising prose."])
    return "\n".join(lines)


def write_pack_files(workspace: Path, pack: dict[str, Any]) -> dict[str, str]:
    paths = {
        "evidence_pack": workspace / "evidence_pack.json",
        "research_brief": workspace / "research_brief.md",
        "handoff": workspace / "handoff.md",
    }
    paths["evidence_pack"].write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["research_brief"].write_text(markdown_brief(pack), encoding="utf-8")
    paths["handoff"].write_text(handoff_markdown(pack), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def resolve_workspace(root: Path, workspace: str) -> Path:
    path = Path(workspace)
    if path.exists():
        return path
    if workspace == "latest":
        candidates = [p for p in (root / "workspaces").iterdir() if p.is_dir()]
        if not candidates:
            raise FileNotFoundError("No RAG workspaces found.")
        return max(candidates, key=lambda p: p.stat().st_mtime)
    candidate = root / "workspaces" / workspace
    if candidate.exists():
        return candidate
    matches = [p for p in (root / "workspaces").glob(f"*{workspace}*") if p.is_dir()]
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"Workspace not found: {workspace}")


def split_claims(text: str) -> list[str]:
    lines = [line.strip(" -\t") for line in text.splitlines() if line.strip()]
    claims: list[str] = []
    for line in lines:
        parts = split_sentences(line)
        claims.extend(parts or [line])
    return claims[:20]


def audit_claims(pack: dict[str, Any], claims: list[str]) -> dict[str, Any]:
    items = pack.get("items", [])
    mapped: list[dict[str, Any]] = []
    for idx, claim in enumerate(claims, start=1):
        candidates = []
        for item in items:
            flags = set(item.get("warning_flags") or [])
            if item.get("source_quality") == "metadata-only":
                status = "metadata-only"
            elif "semantic_excerpt_not_localized" in flags:
                status = "candidate-needs-full-check"
            elif item.get("rerank_status") != "ok":
                status = "candidate-unreranked"
            elif item.get("source_quality") == "verified":
                status = "verified-page-grounded-candidate"
            else:
                status = "candidate"
            candidates.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "title": item.get("title"),
                    "page_range": item.get("page_range"),
                    "status": status,
                    "warning_flags": item.get("warning_flags") or [],
                }
            )
        mapped.append({"claim_id": f"C{idx:02d}", "claim": claim, "candidate_evidence": candidates[:6]})
    warnings = []
    if any(item.get("source_quality") == "metadata-only" for item in items):
        warnings.append(METADATA_ONLY_WARNING)
    if any(item.get("rerank_status") != "ok" for item in items):
        warnings.append(UNRERANKED_WARNING)
    return {"version": "claim_evidence_map_v1", "topic": pack.get("topic"), "claims": mapped, "warnings": warnings}


def audit_markdown(audit: dict[str, Any]) -> str:
    lines = [f"# Writing Audit: {audit.get('topic')}", ""]
    for warning in audit.get("warnings", []):
        lines.extend([warning, ""])
    for claim in audit.get("claims", []):
        lines.append(f"## {claim['claim_id']}. {claim['claim']}")
        for item in claim.get("candidate_evidence", []):
            flags = ", ".join(item.get("warning_flags") or ["none"])
            lines.append(f"- {item.get('evidence_id')}: {item.get('status')} / {item.get('title')} / {item.get('page_range') or 'n/a'} / flags={flags}")
        lines.append("")
    return "\n".join(lines)


def write_audit_files(workspace: Path, audit: dict[str, Any]) -> dict[str, str]:
    paths = {
        "claim_evidence_map": workspace / "claim_evidence_map.json",
        "writing_audit": workspace / "writing_audit.md",
    }
    paths["claim_evidence_map"].write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["writing_audit"].write_text(audit_markdown(audit), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}

