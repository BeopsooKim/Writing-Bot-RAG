from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote, urlparse
from pathlib import Path
from typing import Any, Iterable

from .db import connect, set_sync_state, upsert_document

LINK_RE = re.compile(r"\[(?P<title>[^\]]{8,500})\]\((?P<url>https?://[^)]+)\)")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", re.I)
IEEE_DOC_RE = re.compile(r"ieeexplore\.ieee\.org/(?:abstract/)?document/(?P<id>\d+)", re.I)
SCIENCEDIRECT_PII_RE = re.compile(r"sciencedirect\.com/science/article/pii/(?P<pii>[A-Z0-9]+)", re.I)
IGNORE_TITLES = {
    "저장",
    "Twitter",
    "LinkedIn",
    "Facebook",
    "알리미 목록",
    "알림 취소",
    "모든 추천 자료 보기",
}


def _load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    obj = json.loads(text)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and "responses" in obj:
        return obj["responses"]
    if isinstance(obj, dict) and "messages" in obj:
        return [obj]
    if isinstance(obj, dict):
        return [obj]
    raise ValueError(f"Unsupported Gmail export shape: {path}")


def iter_messages(records: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for record in records:
        if "scholar_alert_items" in record or record.get("source") == "gmail-scholar-parsed":
            continue
        if "messages" in record:
            yield from record.get("messages") or []
        elif "responses" in record:
            for response in record.get("responses") or []:
                yield from response.get("messages") or []
        else:
            yield record


def iter_parsed_items(records: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for record in records:
        if "scholar_alert_items" in record:
            yield from record.get("scholar_alert_items") or []
        elif record.get("source") == "gmail-scholar-parsed" and "items" in record:
            yield from record.get("items") or []
        elif record.get("email_id") and record.get("title") and (
            record.get("publisher_url") or record.get("scholar_url") or record.get("url")
        ):
            yield record


def _clean_title(title: str) -> str:
    title = re.sub(r"^\s*(PDF|HTML)\s*", "", title, flags=re.I)
    return " ".join(title.split())


def normalize_title(title: str) -> str:
    text = _clean_title(title).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _publisher_url(url: str) -> str:
    parsed = urlparse(url)
    if "scholar.google." in parsed.netloc and parsed.path.endswith("/scholar_url"):
        nested = parse_qs(parsed.query).get("url")
        if nested:
            return unquote(nested[0])
    return url


def _is_scholar_recommendation_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if "scholar.google." not in parsed.netloc:
        return False
    query = parse_qs(parsed.query)
    return parsed.path.rstrip("/") == "/scholar" and "sciupd" in query


def _is_pdf_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    return path.endswith(".pdf") or "/pdf/" in path


def _skip_scholar_item(title: str, url: str | None) -> bool:
    if not title or title in IGNORE_TITLES:
        return True
    if title.endswith("추천 자료 보기") or _is_scholar_recommendation_url(url):
        return True
    lowered = (url or "").lower()
    return "scholar_alerts" in lowered or "citations?" in lowered or "scholar_share" in lowered


def _normalized_url_key(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(_publisher_url(url))
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}".lower() or None


def canonical_key(item: dict[str, Any]) -> str:
    urls = [
        item.get("doi"),
        item.get("publisher_url"),
        item.get("url"),
        item.get("pdf_url"),
        item.get("scholar_url"),
    ]
    text = " ".join(str(value) for value in urls + [item.get("title", "")] if value)
    doi = DOI_RE.search(text)
    if doi:
        return f"doi:{doi.group(0).lower().rstrip('.')}"
    arxiv = ARXIV_RE.search(text)
    if arxiv:
        return f"arxiv:{arxiv.group('id').lower()}"
    ieee = IEEE_DOC_RE.search(text)
    if ieee:
        return f"ieee:{ieee.group('id')}"
    pii = SCIENCEDIRECT_PII_RE.search(text)
    if pii:
        return f"pii:{pii.group('pii').lower()}"
    normalized_url = _normalized_url_key(item.get("publisher_url") or item.get("url"))
    if normalized_url:
        return f"url:{normalized_url}"
    return f"title:{normalize_title(item.get('title', 'untitled'))}"


def _alert_name(subject: str) -> str | None:
    return subject.replace(" - 새로운 관련 연구", "").strip() or None


def parse_scholar_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    body = message.get("body") or message.get("snippet") or ""
    email_id = message.get("id") or message.get("message_id") or ""
    subject = message.get("subject") or ""
    lines = body.splitlines()
    results: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        match = LINK_RE.search(line)
        if not match:
            continue
        title = _clean_title(match.group("title"))
        url = _publisher_url(match.group("url"))
        if _skip_scholar_item(title, url):
            continue
        context = "\n".join(lines[idx + 1 : idx + 5])
        year = None
        year_match = YEAR_RE.search(context)
        if year_match:
            year = year_match.group(0)
        authors = None
        venue = None
        if idx + 2 < len(lines):
            meta_line = " ".join(lines[idx + 2].split())
            if " - " in meta_line:
                authors, venue = meta_line.split(" - ", 1)
        results.append(
            {
                "email_id": email_id,
                "email_ts": message.get("email_ts"),
                "alert_name": _alert_name(subject),
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": year,
                "snippet": " ".join(context.split())[:1200],
                "scholar_url": url if "scholar_url" in url else None,
                "publisher_url": url,
                "pdf_url": url if _is_pdf_url(url) else None,
                "raw": {"subject": subject, "url": url},
            }
        )
    return results


def _insert_scholar_item(conn, item: dict[str, Any]) -> int:
    publisher_url = _publisher_url(item.get("publisher_url") or item.get("url") or "")
    email_id = item.get("email_id") or item.get("id") or "unknown-email"
    title = item["title"]
    if _skip_scholar_item(title, publisher_url):
        return 0
    if not item.get("pdf_url") and _is_pdf_url(publisher_url):
        item = {**item, "pdf_url": publisher_url}
    key = canonical_key({**item, "publisher_url": publisher_url})
    doc_id = upsert_document(
        conn,
        source_type="gmail-scholar",
        source_id=f"paper::{key}",
        title=title,
        authors=item.get("authors"),
        year=item.get("year"),
        venue=item.get("venue"),
        url=publisher_url,
        source_family=item.get("source_family") or "Google Scholar Alert",
        status="metadata-only",
        raw_json=item.get("raw") or item,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO scholar_alert_items
        (email_id, email_ts, alert_name, title, authors, venue, year, snippet, scholar_url, publisher_url, pdf_url, document_id, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(email_id, title) DO UPDATE SET
          email_ts=COALESCE(excluded.email_ts, scholar_alert_items.email_ts),
          alert_name=COALESCE(excluded.alert_name, scholar_alert_items.alert_name),
          authors=COALESCE(excluded.authors, scholar_alert_items.authors),
          venue=COALESCE(excluded.venue, scholar_alert_items.venue),
          year=COALESCE(excluded.year, scholar_alert_items.year),
          snippet=COALESCE(excluded.snippet, scholar_alert_items.snippet),
          scholar_url=COALESCE(excluded.scholar_url, scholar_alert_items.scholar_url),
          publisher_url=COALESCE(excluded.publisher_url, scholar_alert_items.publisher_url),
          pdf_url=COALESCE(excluded.pdf_url, scholar_alert_items.pdf_url),
          document_id=excluded.document_id,
          raw_json=COALESCE(excluded.raw_json, scholar_alert_items.raw_json)
        """,
        (
            email_id,
            item.get("email_ts"),
            item.get("alert_name"),
            title,
            item.get("authors"),
            item.get("venue"),
            item.get("year"),
            item.get("snippet"),
            item.get("scholar_url"),
            publisher_url,
            item.get("pdf_url"),
            doc_id,
            json.dumps(item.get("raw") or item, ensure_ascii=False),
        ),
    )
    return doc_id


def ingest_gmail_export(input_path: str | Path, db_path: str | Path, limit: int | None = None) -> dict[str, int]:
    records = _load_records(Path(input_path))
    parsed_items = list(iter_parsed_items(records))
    messages = list(iter_messages(records))
    seen_messages = 0
    inserted_items = 0
    with connect(db_path) as conn:
        for item in parsed_items:
            if limit is not None and inserted_items >= limit:
                break
            doc_id = _insert_scholar_item(conn, item)
            if doc_id:
                inserted_items += 1
        for message in messages:
            seen_messages += 1
            for item in parse_scholar_message(message):
                if limit is not None and inserted_items >= limit:
                    break
                doc_id = _insert_scholar_item(conn, item)
                if doc_id:
                    inserted_items += 1
            if limit is not None and inserted_items >= limit:
                break
        set_sync_state(conn, "gmail.last_ingest_input", str(input_path))
    return {"messages_seen": seen_messages, "items_seen": inserted_items}

