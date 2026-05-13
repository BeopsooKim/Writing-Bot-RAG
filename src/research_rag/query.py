from __future__ import annotations

import csv
from pathlib import Path

from .config import RagConfig
from .db import connect, ensure_work_schema
from .works import duplicate_sources


def _row_source_quality(row: dict[str, object]) -> str:
    if row.get("local_path") and row.get("page_start"):
        return "verified"
    if row.get("local_path"):
        return "rag-supported"
    if row.get("source_type") == "gmail-scholar":
        return "metadata-only"
    return "rag-supported"


def enrich_results(config: RagConfig, rows: list[dict[str, object]]) -> list[dict[str, object]]:
    if not rows:
        return rows
    with connect(config.db_path) as conn:
        ensure_work_schema(conn)
        for row in rows:
            document_id = row.get("document_id")
            if not document_id:
                continue
            doc = conn.execute(
                """
                SELECT d.work_id, d.source_type, d.status, d.content_sha256, w.canonical_key, w.source_quality AS work_source_quality
                FROM documents d
                LEFT JOIN works w ON w.id=d.work_id
                WHERE d.id=?
                """,
                (document_id,),
            ).fetchone()
            if not doc:
                continue
            work_id = int(doc["work_id"]) if doc["work_id"] is not None else None
            row["work_id"] = work_id
            row["canonical_key"] = doc["canonical_key"]
            row["document_status"] = doc["status"]
            row["source_type"] = doc["source_type"]
            row["content_sha256"] = doc["content_sha256"]
            row["source_quality"] = _row_source_quality({**row, "source_type": doc["source_type"]})
            row["work_source_quality"] = doc["work_source_quality"]
            row["page_range"] = (
                f"{row.get('page_start')}-{row.get('page_end') or row.get('page_start')}"
                if row.get("page_start")
                else None
            )
            row["duplicate_sources"] = duplicate_sources(conn, work_id)
    return rows


def search(config: RagConfig, query: str, top_k: int = 10) -> list[dict[str, object]]:
    safe_query = " ".join(query.replace('"', " ").split())
    if not safe_query:
        return []
    with connect(config.db_path) as conn:
        ensure_work_schema(conn)
        rows = conn.execute(
            """
            SELECT
              c.id AS chunk_id,
              d.id AS document_id,
              d.work_id,
              d.source_type,
              d.status AS document_status,
              d.content_sha256,
              w.canonical_key,
              w.source_quality AS work_source_quality,
              d.title,
              d.authors,
              d.year,
              d.venue,
              d.url,
              d.local_path,
              d.source_family,
              c.page_start,
              c.page_end,
              c.section,
              c.chunker_version,
              c.fallback_reason,
              snippet(chunks_fts, 0, '[', ']', ' ... ', 24) AS snippet,
              bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks_fts_map m ON m.rowid = chunks_fts.rowid
            JOIN chunks c ON c.id = m.chunk_id
            JOIN documents d ON d.id = c.document_id
            LEFT JOIN works w ON w.id = d.work_id
            WHERE chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (safe_query, top_k),
        ).fetchall()
        return enrich_results(config, [dict(row) for row in rows])


def write_query_report(config: RagConfig, query: str, rows: list[dict[str, object]]) -> Path:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    stem = "".join(ch if ch.isalnum() else "_" for ch in query.lower())[:80].strip("_") or "query"
    md_path = config.reports_dir / f"{stem}_evidence.md"
    csv_path = config.reports_dir / f"{stem}_evidence.csv"
    fieldnames: list[str] = ["query"]
    if rows:
        seen: set[str] = set()
        fieldnames = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        if rows:
            writer.writerows(rows)
        else:
            writer.writerow({"query": query})
    lines = [f"# Evidence for: {query}", ""]
    if not rows:
        lines.append("No local RAG evidence found.")
    for idx, row in enumerate(rows, start=1):
        pages = ""
        if row.get("page_start"):
            pages = f", pp. {row.get('page_start')}-{row.get('page_end') or row.get('page_start')}"
        lines.extend(
            [
                f"## {idx}. {row.get('title')}{pages}",
                f"- Status: {row.get('source_quality') or 'rag-supported'}",
                f"- Work: {row.get('work_id') or 'unlinked'} ({row.get('canonical_key') or 'no canonical key'})",
                f"- Family: {row.get('source_family') or 'unknown'}",
                f"- Chunker: {row.get('chunker_version') or 'unknown'}; fallback={row.get('fallback_reason') or 'none'}",
                f"- Retrieval: {row.get('retrieval') or 'unknown'}; rerank={row.get('rerank_status') or 'n/a'}",
                f"- Weighted RRF: {row.get('weighted_rrf_score') if row.get('weighted_rrf_score') is not None else 'n/a'}",
                f"- Rerank probability: {row.get('rerank_score_prob') if row.get('rerank_score_prob') is not None else 'n/a'}",
                f"- Relevance: {row.get('relevance_label') or 'Needs Verification'}",
                f"- Verification hint: {row.get('verification_hint') or 'Relevance is not claim support.'}",
                f"- Source: {row.get('url') or row.get('local_path') or 'local'}",
                f"- Snippet: {row.get('snippet') or ''}",
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path

