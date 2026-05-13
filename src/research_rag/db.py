from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id INTEGER,
  source_type TEXT NOT NULL,
  source_id TEXT,
  title TEXT NOT NULL,
  authors TEXT,
  year TEXT,
  venue TEXT,
  doi TEXT,
  url TEXT,
  local_path TEXT,
  drive_path TEXT,
  source_family TEXT,
  status TEXT NOT NULL DEFAULT 'metadata-only',
  content_sha256 TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(work_id) REFERENCES works(id),
  UNIQUE(source_type, source_id)
);

CREATE TABLE IF NOT EXISTS works (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  authors TEXT,
  year TEXT,
  venue TEXT,
  doi TEXT,
  primary_document_id INTEGER,
  source_quality TEXT NOT NULL DEFAULT 'metadata-only',
  raw_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(primary_document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS work_aliases (
  alias_key TEXT PRIMARY KEY,
  work_id INTEGER NOT NULL,
  alias_type TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(work_id) REFERENCES works(id)
);

CREATE TABLE IF NOT EXISTS scholar_alert_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id INTEGER,
  email_id TEXT NOT NULL,
  email_ts TEXT,
  alert_name TEXT,
  title TEXT NOT NULL,
  authors TEXT,
  venue TEXT,
  year TEXT,
  snippet TEXT,
  scholar_url TEXT,
  publisher_url TEXT,
  pdf_url TEXT,
  document_id INTEGER,
  raw_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(work_id) REFERENCES works(id),
  UNIQUE(email_id, title),
  FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER,
  url TEXT NOT NULL,
  status TEXT NOT NULL,
  local_path TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  page_start INTEGER,
  page_end INTEGER,
  section TEXT,
  chunker_version TEXT,
  fallback_reason TEXT,
  token_estimate INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(document_id) REFERENCES documents(id),
  UNIQUE(document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS sync_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  title,
  source_family,
  tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS chunks_fts_map (
  rowid INTEGER PRIMARY KEY,
  chunk_id INTEGER NOT NULL UNIQUE,
  FOREIGN KEY(chunk_id) REFERENCES chunks(id)
);
"""


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        ensure_work_schema(conn)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_work_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS works (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          canonical_key TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          authors TEXT,
          year TEXT,
          venue TEXT,
          doi TEXT,
          primary_document_id INTEGER,
          source_quality TEXT NOT NULL DEFAULT 'metadata-only',
          raw_json TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(primary_document_id) REFERENCES documents(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_aliases (
          alias_key TEXT PRIMARY KEY,
          work_id INTEGER NOT NULL,
          alias_type TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(work_id) REFERENCES works(id)
        )
        """
    )
    _add_column_if_missing(conn, "documents", "work_id", "work_id INTEGER REFERENCES works(id)")
    _add_column_if_missing(conn, "documents", "content_sha256", "content_sha256 TEXT")
    _add_column_if_missing(conn, "scholar_alert_items", "work_id", "work_id INTEGER REFERENCES works(id)")
    _add_column_if_missing(conn, "chunks", "chunker_version", "chunker_version TEXT")
    _add_column_if_missing(conn, "chunks", "fallback_reason", "fallback_reason TEXT")


def upsert_document(conn: sqlite3.Connection, *, source_type: str, source_id: str | None, title: str, **fields) -> int:
    raw_json = fields.pop("raw_json", None)
    raw_json_text = json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None
    values = {
        "source_type": source_type,
        "source_id": source_id,
        "title": title.strip() or "Untitled",
        "authors": fields.get("authors"),
        "year": fields.get("year"),
        "venue": fields.get("venue"),
        "doi": fields.get("doi"),
        "url": fields.get("url"),
        "local_path": fields.get("local_path"),
        "drive_path": fields.get("drive_path"),
        "source_family": fields.get("source_family"),
        "status": fields.get("status", "metadata-only"),
        "raw_json": raw_json_text,
    }
    conn.execute(
        """
        INSERT INTO documents
        (source_type, source_id, title, authors, year, venue, doi, url, local_path, drive_path, source_family, status, raw_json)
        VALUES
        (:source_type, :source_id, :title, :authors, :year, :venue, :doi, :url, :local_path, :drive_path, :source_family, :status, :raw_json)
        ON CONFLICT(source_type, source_id) DO UPDATE SET
          title=excluded.title,
          authors=COALESCE(excluded.authors, documents.authors),
          year=COALESCE(excluded.year, documents.year),
          venue=COALESCE(excluded.venue, documents.venue),
          doi=COALESCE(excluded.doi, documents.doi),
          url=COALESCE(excluded.url, documents.url),
          local_path=COALESCE(excluded.local_path, documents.local_path),
          drive_path=COALESCE(excluded.drive_path, documents.drive_path),
          source_family=COALESCE(excluded.source_family, documents.source_family),
          status=excluded.status,
          raw_json=COALESCE(excluded.raw_json, documents.raw_json),
          updated_at=CURRENT_TIMESTAMP
        """,
        values,
    )
    row = conn.execute(
        "SELECT id FROM documents WHERE source_type=? AND ((source_id IS NULL AND ? IS NULL) OR source_id=?)",
        (source_type, source_id, source_id),
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"])


def set_sync_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_state(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
        """,
        (key, value),
    )

