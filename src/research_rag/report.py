from __future__ import annotations

from pathlib import Path

from .config import RagConfig
from .query import search, write_query_report


def literature_review_prompt_pack(config: RagConfig, topic: str, top_k: int = 20) -> Path:
    rows = search(config, topic, top_k=top_k)
    evidence_path = write_query_report(config, topic, rows)
    prompt_path = config.reports_dir / f"{evidence_path.stem}_prompt_pack.txt"
    lines = [
        "Use the following RAG evidence to draft or revise a literature review.",
        "Rules:",
        "- Do not claim a source supports more than the evidence shows.",
        "- Mark unsupported claims as needs evidence.",
        "- Preserve page-level citations when available.",
        "",
        f"Topic: {topic}",
        "",
        evidence_path.read_text(encoding="utf-8"),
    ]
    prompt_path.write_text("\n".join(lines), encoding="utf-8")
    return prompt_path


