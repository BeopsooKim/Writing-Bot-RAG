from __future__ import annotations

import shutil
from pathlib import Path

from .chunking import chunk_pages
from .config import RagConfig
from .db import connect, init_db
from .pdf_extract import extract_pdf_pages
from .works import file_sha256


def initialize(config: RagConfig) -> Path:
    config.ensure_dirs()
    init_db(config.db_path)
    return config.write()


def import_pdf(config: RagConfig, source: str | Path, title: str | None = None, source_family: str | None = None) -> int:
    from .db import upsert_document

    src = Path(source)
    config.ensure_dirs()
    dest = config.pdf_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    with connect(config.db_path) as conn:
        return upsert_document(
            conn,
            source_type="local-pdf",
            source_id=str(dest),
            title=title or dest.stem,
            local_path=str(dest),
            source_family=source_family,
            status="pdf-available",
        )


def extract_all_pdfs(config: RagConfig, limit: int | None = None, force: bool = False) -> dict[str, int]:
    processed = 0
    skipped = 0
    chunks_written = 0
    fallback_docs = 0
    oversize_count = 0
    with connect(config.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, local_path FROM documents
            WHERE local_path IS NOT NULL AND lower(local_path) LIKE '%.pdf'
              AND status != 'duplicate-pdf'
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            if limit is not None and processed >= limit:
                break
            path = Path(row["local_path"])
            if not path.exists():
                continue
            digest = file_sha256(path)
            if digest:
                conn.execute("UPDATE documents SET content_sha256=? WHERE id=?", (digest, row["id"]))
                duplicate = conn.execute(
                    """
                    SELECT id FROM documents
                    WHERE content_sha256=? AND id != ? AND status='chunked'
                    ORDER BY id
                    LIMIT 1
                    """,
                    (digest, row["id"]),
                ).fetchone()
                if duplicate and not force:
                    conn.execute(
                        "UPDATE documents SET status='duplicate-pdf', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (row["id"],),
                    )
                    skipped += 1
                    continue
            text_path = config.text_dir / f"{row['id']}.txt"
            if not force and text_path.exists():
                existing = conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (row["id"],)).fetchone()[0]
                if existing:
                    skipped += 1
                    continue
            conn.execute("DELETE FROM chunks WHERE document_id=?", (row["id"],))
            pages = extract_pdf_pages(path)
            chunks = chunk_pages(
                pages,
                max_chars=config.max_chars_per_chunk,
                min_chars=config.min_chars_per_chunk,
                overlap=config.overlap_chars,
                hard_max_chars=config.hard_max_chars_per_chunk,
                chunker_version=config.chunker_version,
            )
            doc_fallback = any(chunk.fallback_reason for chunk in chunks)
            if doc_fallback:
                fallback_docs += 1
            oversize_count += sum(1 for chunk in chunks if len(chunk.text) > config.hard_max_chars_per_chunk)
            text_path.write_text("\n\n".join(c.text for c in chunks), encoding="utf-8")
            for idx, chunk in enumerate(chunks):
                conn.execute(
                    """
                    INSERT INTO chunks(document_id, chunk_index, text, page_start, page_end, section, chunker_version, fallback_reason, token_estimate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        idx,
                        chunk.text,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.section,
                        chunk.chunker_version,
                        chunk.fallback_reason,
                        max(1, len(chunk.text) // 4),
                    ),
                )
            conn.execute("UPDATE documents SET status='chunked', updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            processed += 1
            chunks_written += len(chunks)
    return {
        "pdfs_processed": processed,
        "pdfs_skipped": skipped,
        "chunks_written": chunks_written,
        "fallback_docs": fallback_docs,
        "oversize_count": oversize_count,
        "chunker_version": config.chunker_version,
    }

