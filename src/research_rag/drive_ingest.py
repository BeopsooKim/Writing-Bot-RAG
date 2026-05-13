from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import connect, upsert_document


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    obj = json.loads(text)
    if isinstance(obj, dict) and "files" in obj:
        return obj["files"]
    if isinstance(obj, list):
        return obj
    raise ValueError(f"Unsupported Drive manifest shape: {path}")


def _family_from_path(title: str, drive_path: str | None) -> str | None:
    path = drive_path or title
    parts = [p.strip() for p in path.replace("\\", "/").split("/") if p.strip()]
    if "References" in parts:
        idx = parts.index("References")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def ingest_drive_manifest(input_path: str | Path, db_path: str | Path, limit: int | None = None) -> dict[str, int]:
    files = _load_manifest(Path(input_path))
    count = 0
    with connect(db_path) as conn:
        for item in files:
            if limit is not None and count >= limit:
                break
            mime = item.get("mime_type") or item.get("mimeType")
            title = item.get("title") or item.get("name") or "Untitled"
            file_id = item.get("id")
            url = item.get("url") or item.get("webViewLink")
            drive_path = item.get("drive_path") or item.get("path")
            if item.get("file_or_folder") == "folder":
                continue
            if mime and not (
                mime == "application/pdf"
                or mime.startswith("text/")
                or "document" in mime
                or "spreadsheet" in mime
            ):
                continue
            upsert_document(
                conn,
                source_type="google-drive",
                source_id=file_id or url or title,
                title=title,
                url=url,
                drive_path=drive_path,
                source_family=_family_from_path(title, drive_path),
                status="metadata-only",
                raw_json=item,
            )
            count += 1
    return {"files_seen": len(files), "documents_added_or_updated": count}


