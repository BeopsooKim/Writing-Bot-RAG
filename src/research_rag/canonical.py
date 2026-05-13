from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", re.I)
ARXIV_ID_RE = re.compile(r"\barxiv:?\s*(?P<id>\d{4}\.\d{4,5})(?:v\d+)?\b", re.I)
IEEE_DOC_RE = re.compile(r"ieeexplore\.ieee\.org/(?:abstract/)?document/(?P<id>\d+)", re.I)
IEEE_PDF_RE = re.compile(r"ieeexplore\.ieee\.org/.*/(?P<id>\d+)\.pdf", re.I)
SCIENCEDIRECT_PII_RE = re.compile(r"sciencedirect\.com/science/article/pii/(?P<pii>[A-Z0-9]+)", re.I)


def publisher_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if "scholar.google." in parsed.netloc and parsed.path.endswith("/scholar_url"):
        nested = parse_qs(parsed.query).get("url")
        if nested:
            return unquote(nested[0])
    return url


def clean_title(title: str | None) -> str:
    if not title:
        return "Untitled"
    text = Path(str(title)).name
    text = re.sub(r"\.pdf$", "", text, flags=re.I)
    text = re.sub(r"^\s*(?:\[?\s*(PDF|HTML)\s*\]?)\s*", "", text, flags=re.I)
    text = re.sub(r"[_\-]+", " ", text)
    return " ".join(text.split()) or "Untitled"


def normalize_title(title: str | None) -> str:
    text = clean_title(title).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalized_url_key(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(publisher_url(url))
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if not host and not path:
        return None
    return f"url:{host}{path}".lower()


def _dedupe(keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for alias_type, key in keys:
        if key and key not in seen:
            result.append((alias_type, key))
            seen.add(key)
    return result


def canonical_aliases(fields: dict[str, Any]) -> list[tuple[str, str]]:
    title = clean_title(fields.get("title"))
    year = str(fields.get("year") or "").strip()
    urls = [
        fields.get("doi"),
        fields.get("url"),
        fields.get("publisher_url"),
        fields.get("pdf_url"),
        fields.get("scholar_url"),
        fields.get("local_path"),
        fields.get("drive_path"),
    ]
    text = " ".join(str(value) for value in [*urls, title] if value)
    keys: list[tuple[str, str]] = []
    doi = DOI_RE.search(text)
    if doi:
        keys.append(("doi", f"doi:{doi.group(0).lower().rstrip('.')}"))
    arxiv = ARXIV_RE.search(text) or ARXIV_ID_RE.search(text)
    if arxiv:
        keys.append(("arxiv", f"arxiv:{arxiv.group('id').lower()}"))
    ieee = IEEE_DOC_RE.search(text) or IEEE_PDF_RE.search(text)
    if ieee:
        keys.append(("ieee", f"ieee:{ieee.group('id')}"))
    pii = SCIENCEDIRECT_PII_RE.search(text)
    if pii:
        keys.append(("pii", f"pii:{pii.group('pii').lower()}"))
    for url in [fields.get("url"), fields.get("publisher_url"), fields.get("pdf_url")]:
        url_key = normalized_url_key(url)
        if url_key:
            keys.append(("url", url_key))
    title_key = normalize_title(title)
    if title_key:
        suffix = f":{year}" if year else ""
        keys.append(("title", f"title:{title_key}{suffix}"))
    return _dedupe(keys)


def primary_canonical_key(fields: dict[str, Any]) -> str:
    aliases = canonical_aliases(fields)
    if aliases:
        return aliases[0][1]
    return "title:untitled"

