from __future__ import annotations

import time
from pathlib import Path

from .config import RagConfig
from .db import connect


def safe_resolve_papers(config: RagConfig, limit: int | None = None, delay_s: float = 2.0) -> dict[str, int]:
    """Attempt direct PDF URL downloads only; do not bypass login, CAPTCHA, or DRM."""

    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install requests before running resolve-papers.") from exc

    config.ensure_dirs()
    attempted = 0
    downloaded = 0
    queued = 0
    with connect(config.db_path) as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.title, s.pdf_url, s.publisher_url
            FROM scholar_alert_items s
            JOIN documents d ON d.id = s.document_id
            WHERE d.local_path IS NULL
            ORDER BY s.email_ts DESC, s.id DESC
            """
        ).fetchall()
        for row in rows:
            if limit is not None and attempted >= limit:
                break
            url = row["pdf_url"]
            if not url:
                queued += 1
                conn.execute(
                    "INSERT INTO downloads(document_id, url, status, note) VALUES (?, ?, ?, ?)",
                    (row["id"], row["publisher_url"], "manual-review", "No direct PDF URL in Scholar alert."),
                )
                continue
            attempted += 1
            try:
                response = requests.get(url, timeout=30, headers={"User-Agent": "ResearchRAG/0.1"})
                ctype = response.headers.get("content-type", "").lower()
                if response.status_code == 200 and ("pdf" in ctype or response.content[:4] == b"%PDF"):
                    name = f"{row['id']}_{''.join(ch if ch.isalnum() else '_' for ch in row['title'])[:80]}.pdf"
                    dest = config.pdf_dir / name
                    dest.write_bytes(response.content)
                    conn.execute(
                        "UPDATE documents SET local_path=?, status='pdf-available', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(dest), row["id"]),
                    )
                    conn.execute(
                        "INSERT INTO downloads(document_id, url, status, local_path) VALUES (?, ?, ?, ?)",
                        (row["id"], url, "downloaded", str(dest)),
                    )
                    downloaded += 1
                else:
                    conn.execute(
                        "INSERT INTO downloads(document_id, url, status, note) VALUES (?, ?, ?, ?)",
                        (row["id"], url, "manual-review", f"HTTP {response.status_code}; content-type={ctype}"),
                    )
            except Exception as exc:
                conn.execute(
                    "INSERT INTO downloads(document_id, url, status, note) VALUES (?, ?, ?, ?)",
                    (row["id"], url, "error", str(exc)[:500]),
                )
            time.sleep(max(0.0, delay_s))
    return {"attempted": attempted, "downloaded": downloaded, "manual_review": queued}

