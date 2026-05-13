from __future__ import annotations

from pathlib import Path


def _extract_with_pymupdf(path: Path) -> list[tuple[int, str]]:
    import fitz  # type: ignore

    pages: list[tuple[int, str]] = []
    with fitz.open(path) as doc:
        for idx, page in enumerate(doc, start=1):
            pages.append((idx, page.get_text("text") or ""))
    return pages


def _extract_with_pypdf(path: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [(idx, page.extract_text() or "") for idx, page in enumerate(reader.pages, start=1)]


def extract_pdf_pages(path: str | Path) -> list[tuple[int, str]]:
    pdf_path = Path(path)
    try:
        return _extract_with_pymupdf(pdf_path)
    except Exception:
        return _extract_with_pypdf(pdf_path)


