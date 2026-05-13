from __future__ import annotations

from .config import RagConfig
from .db import connect


def build_fts_index(config: RagConfig) -> dict[str, int]:
    with connect(config.db_path) as conn:
        conn.execute("DELETE FROM chunks_fts")
        conn.execute("DELETE FROM chunks_fts_map")
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.text, d.title, d.source_family
            FROM chunks c JOIN documents d ON d.id = c.document_id
            ORDER BY c.id
            """
        ).fetchall()
        for row in rows:
            cur = conn.execute(
                "INSERT INTO chunks_fts(text, title, source_family) VALUES (?, ?, ?)",
                (row["text"], row["title"], row["source_family"] or ""),
            )
            conn.execute(
                "INSERT INTO chunks_fts_map(rowid, chunk_id) VALUES (?, ?)",
                (cur.lastrowid, row["chunk_id"]),
            )
    return {"chunks_indexed": len(rows)}


