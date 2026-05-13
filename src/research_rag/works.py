from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .canonical import canonical_aliases, clean_title, primary_canonical_key
from .db import connect, ensure_work_schema


PDF_SOURCE_PRIORITY = {
    "google-drive": 100,
    "local-reference": 95,
    "local-pdf": 80,
    "gmail-scholar": 70,
}


def file_sha256(path: str | Path | None) -> str | None:
    if not path:
        return None
    pdf = Path(path)
    if not pdf.exists() or not pdf.is_file():
        return None
    digest = hashlib.sha256()
    with pdf.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_priority(row: dict[str, Any]) -> int:
    status = row.get("status") or "metadata-only"
    source_type = row.get("source_type") or ""
    score = PDF_SOURCE_PRIORITY.get(source_type, 40)
    if status == "chunked":
        score += 30
    elif status == "pdf-available":
        score += 20
    elif status == "duplicate-pdf":
        score += 10
    return score


def source_quality(row: dict[str, Any]) -> str:
    status = row.get("status") or "metadata-only"
    if status == "chunked" and row.get("local_path"):
        return "verified"
    if status in {"pdf-available", "duplicate-pdf"} and row.get("local_path"):
        return "rag-supported"
    if row.get("source_type") == "gmail-scholar":
        return "metadata-only"
    return "metadata-only"


def _find_existing_work(conn, aliases: list[tuple[str, str]], content_sha256: str | None) -> int | None:
    if content_sha256:
        row = conn.execute(
            """
            SELECT work_id FROM documents
            WHERE content_sha256=? AND work_id IS NOT NULL
            ORDER BY CASE status WHEN 'chunked' THEN 0 WHEN 'pdf-available' THEN 1 ELSE 2 END, id
            LIMIT 1
            """,
            (content_sha256,),
        ).fetchone()
        if row:
            return int(row["work_id"])
    for _, alias_key in aliases:
        row = conn.execute("SELECT work_id FROM work_aliases WHERE alias_key=?", (alias_key,)).fetchone()
        if row:
            return int(row["work_id"])
    return None


def _create_work(conn, row: dict[str, Any], canonical_key: str) -> int:
    conn.execute(
        """
        INSERT INTO works(canonical_key, title, authors, year, venue, doi, source_quality, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
          title=COALESCE(works.title, excluded.title),
          authors=COALESCE(works.authors, excluded.authors),
          year=COALESCE(works.year, excluded.year),
          venue=COALESCE(works.venue, excluded.venue),
          doi=COALESCE(works.doi, excluded.doi),
          updated_at=CURRENT_TIMESTAMP
        """,
        (
            canonical_key,
            clean_title(row.get("title")),
            row.get("authors"),
            row.get("year"),
            row.get("venue"),
            row.get("doi"),
            source_quality(row),
            json.dumps({"first_document_id": row.get("id")}, ensure_ascii=False),
        ),
    )
    found = conn.execute("SELECT id FROM works WHERE canonical_key=?", (canonical_key,)).fetchone()
    return int(found["id"])


def _upsert_aliases(conn, work_id: int, aliases: list[tuple[str, str]]) -> None:
    for alias_type, alias_key in aliases:
        conn.execute(
            """
            INSERT INTO work_aliases(alias_key, work_id, alias_type)
            VALUES (?, ?, ?)
            ON CONFLICT(alias_key) DO NOTHING
            """,
            (alias_key, work_id, alias_type),
        )


def _refresh_primary_documents(conn) -> None:
    work_rows = conn.execute("SELECT id FROM works").fetchall()
    for work in work_rows:
        docs = [dict(row) for row in conn.execute("SELECT * FROM documents WHERE work_id=?", (work["id"],)).fetchall()]
        if not docs:
            continue
        primary = sorted(docs, key=source_priority, reverse=True)[0]
        quality_order = {"verified": 3, "rag-supported": 2, "metadata-only": 1}
        best_quality = sorted((source_quality(doc) for doc in docs), key=lambda q: quality_order.get(q, 0), reverse=True)[0]
        conn.execute(
            """
            UPDATE works
            SET primary_document_id=?,
                source_quality=?,
                title=COALESCE(NULLIF(?, ''), title),
                authors=COALESCE(?, authors),
                year=COALESCE(?, year),
                venue=COALESCE(?, venue),
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                primary["id"],
                best_quality,
                clean_title(primary.get("title")),
                primary.get("authors"),
                primary.get("year"),
                primary.get("venue"),
                work["id"],
            ),
        )


def _duplicate_pdf_ids(conn) -> list[int]:
    rows = conn.execute(
        """
        SELECT content_sha256
        FROM documents
        WHERE content_sha256 IS NOT NULL AND content_sha256 != ''
        GROUP BY content_sha256
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    duplicate_ids: list[int] = []
    for row in rows:
        docs = [
            dict(doc)
            for doc in conn.execute(
                """
                SELECT * FROM documents
                WHERE content_sha256=?
                ORDER BY CASE status WHEN 'chunked' THEN 0 WHEN 'pdf-available' THEN 1 ELSE 2 END,
                         CASE source_type WHEN 'google-drive' THEN 0 WHEN 'local-reference' THEN 1 ELSE 2 END,
                         id
                """,
                (row["content_sha256"],),
            ).fetchall()
        ]
        for doc in docs[1:]:
            duplicate_ids.append(int(doc["id"]))
    return duplicate_ids


def build_work_layer(db_path: str | Path, *, compute_hashes: bool = True) -> dict[str, Any]:
    with connect(db_path) as conn:
        ensure_work_schema(conn)
        rows = [dict(row) for row in conn.execute("SELECT * FROM documents ORDER BY id").fetchall()]
        works_created_before = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        aliases_before = conn.execute("SELECT COUNT(*) FROM work_aliases").fetchone()[0]
        hashes_computed = 0
        documents_linked = 0
        for row in rows:
            content_sha256 = row.get("content_sha256")
            if compute_hashes and row.get("local_path") and not content_sha256:
                content_sha256 = file_sha256(row.get("local_path"))
                if content_sha256:
                    conn.execute("UPDATE documents SET content_sha256=? WHERE id=?", (content_sha256, row["id"]))
                    row["content_sha256"] = content_sha256
                    hashes_computed += 1
            aliases = canonical_aliases(row)
            canonical_key = primary_canonical_key(row)
            work_id = _find_existing_work(conn, aliases, content_sha256)
            if work_id is None:
                work_id = _create_work(conn, row, canonical_key)
            _upsert_aliases(conn, work_id, aliases)
            conn.execute("UPDATE documents SET work_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (work_id, row["id"]))
            documents_linked += 1
        conn.execute(
            """
            UPDATE scholar_alert_items
            SET work_id=(SELECT work_id FROM documents WHERE documents.id=scholar_alert_items.document_id)
            WHERE document_id IS NOT NULL
            """
        )
        duplicate_ids = _duplicate_pdf_ids(conn)
        for doc_id in duplicate_ids:
            conn.execute(
                """
                UPDATE documents
                SET status='duplicate-pdf', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status != 'chunked'
                """,
                (doc_id,),
            )
        _refresh_primary_documents(conn)
        works_after = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        aliases_after = conn.execute("SELECT COUNT(*) FROM work_aliases").fetchone()[0]
        metadata_only = conn.execute(
            "SELECT COUNT(*) FROM works WHERE source_quality='metadata-only'"
        ).fetchone()[0]
        verified = conn.execute("SELECT COUNT(*) FROM works WHERE source_quality='verified'").fetchone()[0]
        rag_supported = conn.execute(
            "SELECT COUNT(*) FROM works WHERE source_quality='rag-supported'"
        ).fetchone()[0]
        return {
            "documents_seen": len(rows),
            "documents_linked": documents_linked,
            "works_created": works_after - works_created_before,
            "works_total": works_after,
            "aliases_added": aliases_after - aliases_before,
            "aliases_total": aliases_after,
            "hashes_computed": hashes_computed,
            "duplicate_pdf_documents": len(duplicate_ids),
            "works_by_quality": {
                "verified": verified,
                "rag-supported": rag_supported,
                "metadata-only": metadata_only,
            },
        }


def duplicate_sources(conn, work_id: int | None) -> list[dict[str, Any]]:
    if not work_id:
        return []
    rows = conn.execute(
        """
        SELECT source_type, status, source_family, COUNT(*) AS count
        FROM documents
        WHERE work_id=?
        GROUP BY source_type, status, source_family
        ORDER BY source_type, status, source_family
        """,
        (work_id,),
    ).fetchall()
    return [dict(row) for row in rows]

