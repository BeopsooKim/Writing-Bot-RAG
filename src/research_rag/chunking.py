from __future__ import annotations

import re
import time
from dataclasses import dataclass


ACADEMIC_CHUNKER_VERSION = "academic-semantic-v1"
FALLBACK_CHUNKER_VERSION = "character-fallback-v1"

CAPTION_RE = re.compile(r"^\s*(?:Table|TABLE|Fig\.?|Figure)\s+(?:[IVXLCDM]+|\d+)[\.:)\-\s]", re.IGNORECASE)
EQUATION_REF_RE = re.compile(r"\b(?:Eq\.?|Equation|from|using|in)\s*\(?\d+[a-z]?\)?", re.IGNORECASE)
FORMULA_SYMBOL_RE = re.compile(r"(?:[=≤≥≈∑∫√∆ΔΩωαβγλμθπ]|\\sum|\\int|\\frac|\([0-9]{1,3}[a-z]?\)\s*$)")
WHERE_RE = re.compile(r"^\s*(?:where|in which|subject to|s\.t\.|as shown in Eq|from\s*\(\d+|using\s*\(\d+)\b", re.IGNORECASE)
HEADING_RE = re.compile(
    r"^\s*(?:[0-9IVXLCDM]+(?:\.[0-9]+)*\.?\s+)?"
    r"(?:Abstract|Introduction|Background|Methodology|Methods?|Results?|Discussion|Conclusion|References|Appendix|"
    r"[A-Z][A-Z0-9 /,&\-]{4,80})\s*$"
)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass(frozen=True)
class Chunk:
    text: str
    page_start: int | None
    page_end: int | None
    section: str | None = None
    chunker_version: str = ACADEMIC_CHUNKER_VERSION
    fallback_reason: str | None = None


@dataclass
class _Block:
    text: str
    page_start: int | None
    page_end: int | None
    kind: str = "paragraph"
    section: str | None = None
    protected: bool = False


class _ChunkingTimeout(RuntimeError):
    pass


def _normalize_inline(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.strip())


def _normalize_block(lines: list[str]) -> str:
    cleaned = [_normalize_inline(line) for line in lines if line.strip()]
    return "\n".join(cleaned).strip()


def _is_heading(line: str) -> bool:
    stripped = _normalize_inline(line)
    if not stripped or len(stripped) > 100:
        return False
    if CAPTION_RE.search(stripped):
        return False
    return bool(HEADING_RE.search(stripped))


def _heading_title(line: str) -> str:
    return re.sub(r"^\s*[0-9IVXLCDM]+(?:\.[0-9]+)*\.?\s+", "", _normalize_inline(line), flags=re.IGNORECASE)


def _is_caption(line: str) -> bool:
    return bool(CAPTION_RE.search(_normalize_inline(line)))


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    numeric_tokens = re.findall(r"[-+]?\d+(?:\.\d+)?%?", stripped)
    has_units = bool(re.search(r"\b(?:pu|p\.u\.|kV|kA|MW|Mvar|Hz|A|V|%)\b", stripped, re.IGNORECASE))
    column_like = "\t" in line or bool(re.search(r"\S\s{3,}\S", line))
    return (column_like and len(stripped) <= 240) or (len(numeric_tokens) >= 2 and has_units)


def _is_footnote(line: str) -> bool:
    return bool(re.search(r"^\s*(?:note|notes|source|where|\*|†|[a-z]\))\b", line.strip(), re.IGNORECASE))


def _is_formula_line(line: str) -> bool:
    stripped = _normalize_inline(line)
    if not stripped or len(stripped) > 260:
        return False
    if _is_caption(stripped) or _is_table_row(line):
        return False
    if EQUATION_REF_RE.search(stripped) and FORMULA_SYMBOL_RE.search(stripped):
        return True
    if FORMULA_SYMBOL_RE.search(stripped) and len(re.findall(r"[A-Za-z]", stripped)) <= max(18, len(stripped) // 3):
        return True
    return False


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise _ChunkingTimeout("chunking time budget exceeded")


def _split_last_sentence(text: str) -> tuple[str, str]:
    pieces = re.split(SENTENCE_BOUNDARY_RE, text.strip())
    if len(pieces) <= 1:
        return "", text.strip()
    prefix = " ".join(pieces[:-1]).strip()
    suffix = pieces[-1].strip()
    return prefix, suffix


def _sentence_tail(text: str, overlap: int) -> str:
    if not overlap or len(text) <= overlap:
        return text.strip()
    tail = text[-overlap:].strip()
    boundary = re.search(SENTENCE_BOUNDARY_RE, tail)
    if boundary:
        return tail[boundary.end() :].strip()
    return tail


def _best_split_index(text: str, max_chars: int) -> int:
    window = text[:max_chars]
    candidates = [
        max(window.rfind("\n\n"), window.rfind("\n")),
        max(match.end() for match in SENTENCE_BOUNDARY_RE.finditer(window)) if SENTENCE_BOUNDARY_RE.search(window) else -1,
        max(window.rfind("). "), window.rfind("]. "), window.rfind("; "), window.rfind(", ")),
        window.rfind(" "),
    ]
    for idx in candidates:
        if idx >= max(300, max_chars // 3):
            return idx
    return max_chars


def _character_fallback_chunks(
    pages: list[tuple[int, str]],
    *,
    max_chars: int,
    min_chars: int,
    overlap: int,
    hard_max_chars: int,
    fallback_reason: str,
) -> list[Chunk]:
    limit = min(max_chars, hard_max_chars)
    chunks: list[Chunk] = []
    buf = ""
    start_page: int | None = None
    end_page: int | None = None
    for page_num, text in pages:
        normalized = " ".join((text or "").split())
        if not normalized:
            continue
        if start_page is None:
            start_page = page_num
        end_page = page_num
        if len(buf) + len(normalized) + 1 <= limit:
            buf = f"{buf} {normalized}".strip()
            continue
        if len(buf) >= min_chars:
            chunks.append(
                Chunk(
                    buf[:hard_max_chars],
                    start_page,
                    end_page,
                    chunker_version=FALLBACK_CHUNKER_VERSION,
                    fallback_reason=fallback_reason,
                )
            )
        tail = buf[-overlap:] if overlap and buf else ""
        buf = f"{tail} {normalized}".strip()
        start_page = page_num
        end_page = page_num
        while len(buf) > limit:
            piece = buf[:limit]
            chunks.append(
                Chunk(
                    piece,
                    start_page,
                    end_page,
                    chunker_version=FALLBACK_CHUNKER_VERSION,
                    fallback_reason=fallback_reason,
                )
            )
            buf = f"{piece[-overlap:]} {buf[limit:]}".strip()
    if len(buf) >= min_chars or (buf and not chunks):
        chunks.append(
            Chunk(
                buf[:hard_max_chars],
                start_page,
                end_page,
                chunker_version=FALLBACK_CHUNKER_VERSION,
                fallback_reason=fallback_reason,
            )
        )
    return chunks


def _split_text_block(
    block: _Block,
    *,
    max_chars: int,
    hard_max_chars: int,
    overlap: int,
    fallback_reason: str | None = None,
) -> list[Chunk]:
    text = block.text.strip()
    if not text:
        return []
    chunks: list[Chunk] = []
    while len(text) > hard_max_chars:
        idx = _best_split_index(text, max_chars)
        piece = text[:idx].strip()
        if piece:
            chunks.append(
                Chunk(
                    piece[:hard_max_chars],
                    block.page_start,
                    block.page_end,
                    section=block.section,
                    chunker_version=FALLBACK_CHUNKER_VERSION if fallback_reason else ACADEMIC_CHUNKER_VERSION,
                    fallback_reason=fallback_reason,
                )
            )
        tail = _sentence_tail(piece, overlap)
        text = f"{tail}\n{text[idx:].strip()}".strip() if tail else text[idx:].strip()
        if idx <= 0:
            break
    if text:
        chunks.append(
            Chunk(
                text[:hard_max_chars],
                block.page_start,
                block.page_end,
                section=block.section,
                chunker_version=FALLBACK_CHUNKER_VERSION if fallback_reason else ACADEMIC_CHUNKER_VERSION,
                fallback_reason=fallback_reason,
            )
        )
    return chunks


def _collect_blocks(
    pages: list[tuple[int, str]],
    *,
    hard_max_chars: int,
    deadline: float | None,
) -> list[_Block]:
    blocks: list[_Block] = []
    paragraph: list[str] = []
    paragraph_start: int | None = None
    current_section: str | None = None

    def flush_paragraph(page_end: int | None = None) -> None:
        nonlocal paragraph, paragraph_start
        text = _normalize_block(paragraph)
        if text:
            blocks.append(_Block(text, paragraph_start, page_end or paragraph_start, "paragraph", current_section))
        paragraph = []
        paragraph_start = None

    for page_num, page_text in pages:
        _check_deadline(deadline)
        lines = (page_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        i = 0
        while i < len(lines):
            _check_deadline(deadline)
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                flush_paragraph(page_num)
                i += 1
                continue
            if _is_heading(stripped):
                flush_paragraph(page_num)
                current_section = _heading_title(stripped)
                blocks.append(_Block(current_section, page_num, page_num, "heading", current_section, protected=True))
                i += 1
                continue
            if _is_caption(stripped):
                flush_paragraph(page_num)
                capsule = [line]
                i += 1
                while i < len(lines) and len("\n".join(capsule)) <= hard_max_chars * 2:
                    next_line = lines[i]
                    next_stripped = next_line.strip()
                    if not next_stripped:
                        if len(capsule) > 1:
                            capsule.append(next_line)
                            i += 1
                            break
                        i += 1
                        continue
                    if _is_caption(next_stripped) or _is_heading(next_stripped):
                        break
                    if _is_table_row(next_line) or _is_footnote(next_line) or len(capsule) < 8:
                        capsule.append(next_line)
                        i += 1
                        continue
                    break
                blocks.append(_Block(_normalize_block(capsule), page_num, page_num, "table", current_section, protected=True))
                continue
            if _is_formula_line(line):
                flush_paragraph(page_num)
                formula = [line]
                i += 1
                while i < len(lines) and _is_formula_line(lines[i]):
                    formula.append(lines[i])
                    i += 1
                blocks.append(_Block(_normalize_block(formula), page_num, page_num, "formula", current_section, protected=True))
                continue
            if paragraph_start is None:
                paragraph_start = page_num
            paragraph.append(line)
            i += 1
        flush_paragraph(page_num)
    return blocks


def _merge_formula_context(blocks: list[_Block]) -> list[_Block]:
    merged: list[_Block] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.kind != "formula":
            merged.append(block)
            i += 1
            continue

        text_parts: list[str] = []
        page_start = block.page_start
        if merged and merged[-1].kind == "paragraph":
            prev = merged[-1]
            prefix, suffix = _split_last_sentence(prev.text)
            if suffix:
                text_parts.append(suffix)
                page_start = prev.page_start
                if prefix:
                    prev.text = prefix
                else:
                    merged.pop()

        text_parts.append(block.text)
        page_end = block.page_end
        j = i + 1
        while j < len(blocks) and blocks[j].kind == "paragraph" and WHERE_RE.search(blocks[j].text):
            text_parts.append(blocks[j].text)
            page_end = blocks[j].page_end
            j += 1

        merged.append(
            _Block(
                "\n".join(part for part in text_parts if part).strip(),
                page_start,
                page_end,
                "formula",
                block.section,
                protected=True,
            )
        )
        i = j
    return merged


def _chunk_blocks(
    blocks: list[_Block],
    *,
    max_chars: int,
    min_chars: int,
    overlap: int,
    hard_max_chars: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf = ""
    start_page: int | None = None
    end_page: int | None = None
    section: str | None = None

    def emit() -> None:
        nonlocal buf, start_page, end_page, section
        text = buf.strip()
        if text and (len(text) >= min_chars or not chunks):
            chunks.extend(
                _split_text_block(
                    _Block(text, start_page, end_page, "paragraph", section),
                    max_chars=max_chars,
                    hard_max_chars=hard_max_chars,
                    overlap=overlap,
                )
            )
        buf = ""
        start_page = None
        end_page = None
        section = None

    for block in blocks:
        if not block.text.strip():
            continue
        pieces = (
            _split_text_block(
                block,
                max_chars=max_chars,
                hard_max_chars=hard_max_chars,
                overlap=overlap,
                fallback_reason="protected_block_oversize" if len(block.text) > hard_max_chars else None,
            )
            if len(block.text) > hard_max_chars
            else [
                Chunk(
                    block.text,
                    block.page_start,
                    block.page_end,
                    section=block.section,
                    chunker_version=ACADEMIC_CHUNKER_VERSION,
                )
            ]
        )
        for piece in pieces:
            piece_text = piece.text.strip()
            if not piece_text:
                continue
            if piece.fallback_reason:
                emit()
                chunks.append(piece)
                continue
            candidate_len = len(buf) + len(piece_text) + 2
            if buf and candidate_len > max_chars:
                tail = _sentence_tail(buf, overlap)
                emit()
                if tail and len(tail) < max_chars // 2:
                    buf = tail
                    start_page = piece.page_start
                    end_page = piece.page_end
                    section = piece.section
            if start_page is None:
                start_page = piece.page_start
            end_page = piece.page_end or end_page
            section = section or piece.section
            buf = f"{buf}\n\n{piece_text}".strip()
            if len(buf) > hard_max_chars:
                emit()
    emit()
    return chunks


def _oversize_count(chunks: list[Chunk], hard_max_chars: int) -> int:
    return sum(1 for chunk in chunks if len(chunk.text) > hard_max_chars)


def chunk_pages(
    pages: list[tuple[int, str]],
    *,
    max_chars: int = 2200,
    min_chars: int = 300,
    overlap: int = 360,
    hard_max_chars: int = 3200,
    chunker_version: str = ACADEMIC_CHUNKER_VERSION,
    time_budget_s: float | None = None,
) -> list[Chunk]:
    if chunker_version == FALLBACK_CHUNKER_VERSION:
        return _character_fallback_chunks(
            pages,
            max_chars=max_chars,
            min_chars=min_chars,
            overlap=overlap,
            hard_max_chars=hard_max_chars,
            fallback_reason="requested_character_fallback",
        )

    budget = time_budget_s if time_budget_s is not None else max(15.0, len(pages) * 0.25)
    deadline = time.monotonic() + budget if budget > 0 else None
    try:
        blocks = _collect_blocks(pages, hard_max_chars=hard_max_chars, deadline=deadline)
        _check_deadline(deadline)
        chunks = _chunk_blocks(
            _merge_formula_context(blocks),
            max_chars=max_chars,
            min_chars=min_chars,
            overlap=overlap,
            hard_max_chars=hard_max_chars,
        )
        if _oversize_count(chunks, hard_max_chars):
            raise RuntimeError("academic chunker produced oversize chunks")
        return chunks
    except Exception as exc:
        reason = "time_budget_exceeded" if isinstance(exc, _ChunkingTimeout) else f"academic_chunker_failed:{type(exc).__name__}"
        return _character_fallback_chunks(
            pages,
            max_chars=max_chars,
            min_chars=min_chars,
            overlap=overlap,
            hard_max_chars=hard_max_chars,
            fallback_reason=reason,
        )

