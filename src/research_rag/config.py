from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


WINDOWS_DEFAULT_ROOT = Path(r"D:\Research_RAG")


def default_root() -> Path:
    env_root = os.environ.get("RESEARCH_RAG_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if WINDOWS_DEFAULT_ROOT.exists():
        return WINDOWS_DEFAULT_ROOT.resolve()
    return (Path.home() / ".research_rag").resolve()


DEFAULT_ROOT = default_root()


@dataclass(frozen=True)
class RagConfig:
    root: Path = DEFAULT_ROOT
    db_path: Path = DEFAULT_ROOT / "metadata" / "rag.sqlite"
    pdf_dir: Path = DEFAULT_ROOT / "corpus" / "pdfs"
    text_dir: Path = DEFAULT_ROOT / "corpus" / "text"
    index_dir: Path = DEFAULT_ROOT / "indexes"
    reports_dir: Path = DEFAULT_ROOT / "reports"
    samples_dir: Path = DEFAULT_ROOT / "samples"
    max_chars_per_chunk: int = 2200
    overlap_chars: int = 360
    min_chars_per_chunk: int = 300
    hard_max_chars_per_chunk: int = 3200
    chunker_version: str = "academic-semantic-v1"

    @classmethod
    def from_root(cls, root: str | Path | None) -> "RagConfig":
        resolved = Path(root).expanduser().resolve() if root else default_root()
        return cls(
            root=resolved,
            db_path=resolved / "metadata" / "rag.sqlite",
            pdf_dir=resolved / "corpus" / "pdfs",
            text_dir=resolved / "corpus" / "text",
            index_dir=resolved / "indexes",
            reports_dir=resolved / "reports",
            samples_dir=resolved / "samples",
        )

    def ensure_dirs(self) -> None:
        for path in [
            self.root,
            self.db_path.parent,
            self.pdf_dir,
            self.text_dir,
            self.index_dir,
            self.reports_dir,
            self.samples_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def write(self) -> Path:
        self.ensure_dirs()
        path = self.root / "rag_config.json"
        data = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self).items()}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

