from __future__ import annotations

import json
import zipfile
from html import escape
from pathlib import Path
from typing import Any

from .db import connect


FAILURE_COLUMNS = ["title", "folder", "drive_id", "url", "reason", "retry_count", "local_path", "timestamp"]


def _path_variants(path: str | Path) -> list[str]:
    text = str(path).rstrip("\\/")
    variants = [text, text.replace("\\", "/")]
    lowered: list[str] = []
    for item in variants:
        if item and item not in lowered:
            lowered.append(item)
    return lowered


def _rewrite_rooted_path(value: str | None, old_root: str | Path, new_root: str | Path) -> str | None:
    if not value:
        return None
    raw = str(value)
    normalized = raw.replace("\\", "/")
    for old_variant in _path_variants(old_root):
        old_normalized = old_variant.replace("\\", "/").rstrip("/")
        if normalized.lower() == old_normalized.lower():
            return str(Path(new_root))
        prefix = old_normalized + "/"
        if normalized.lower().startswith(prefix.lower()):
            suffix = normalized[len(prefix) :]
            return str(Path(new_root).joinpath(*[part for part in suffix.split("/") if part]))
    return None


def migrate_paths(
    db_path: str | Path,
    *,
    old_root: str | Path,
    new_root: str | Path,
    apply: bool = False,
    example_limit: int = 10,
) -> dict[str, Any]:
    targets = [
        ("documents", "local_path", None),
        ("downloads", "local_path", None),
        ("documents", "source_id", "source_type='local-pdf'"),
    ]
    examples: list[dict[str, Any]] = []
    changed_by_table: dict[str, int] = {}
    with connect(db_path) as conn:
        for table, column, where in targets:
            changed = 0
            where_sql = f"{column} IS NOT NULL" if where is None else f"{column} IS NOT NULL AND {where}"
            rows = conn.execute(f"SELECT id, {column} FROM {table} WHERE {where_sql}").fetchall()
            for row in rows:
                rewritten = _rewrite_rooted_path(row[column], old_root, new_root)
                if not rewritten or rewritten == row[column]:
                    continue
                changed += 1
                if len(examples) < example_limit:
                    examples.append(
                        {
                            "table": table,
                            "id": int(row["id"]),
                            "old_path": row[column],
                            "new_path": rewritten,
                            "new_exists": Path(rewritten).exists(),
                        }
                    )
                if apply:
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (rewritten, row["id"]))
            changed_by_table[f"{table}.{column}"] = changed
    return {
        "status": "applied" if apply else "dry-run",
        "old_root": str(old_root),
        "new_root": str(new_root),
        "changed_total": sum(changed_by_table.values()),
        "changed_by_table": changed_by_table,
        "examples": examples,
    }


def attach_drive_pdf(
    db_path: str | Path,
    *,
    drive_id: str,
    local_path: str | Path,
    title: str | None = None,
    source_family: str | None = None,
) -> dict[str, Any]:
    pdf = Path(local_path)
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, title FROM documents WHERE source_type='google-drive' AND source_id=?",
            (drive_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No google-drive document found for drive_id={drive_id}")
        conn.execute(
            """
            UPDATE documents
            SET title=COALESCE(?, title),
                local_path=?,
                source_family=COALESCE(?, source_family),
                status='pdf-available',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (title, str(pdf), source_family, row["id"]),
        )
        return {"document_id": int(row["id"]), "title": title or row["title"], "local_path": str(pdf)}


def remove_local_pdf_duplicate(db_path: str | Path, *, local_path: str | Path) -> dict[str, Any]:
    path = str(Path(local_path))
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM documents WHERE source_type='local-pdf' AND local_path=?",
            (path,),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        for doc_id in ids:
            conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        return {"removed_local_pdf_documents": ids}


def _load_failure_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, dict) and "failures" in obj:
        return list(obj["failures"])
    if isinstance(obj, list):
        return obj
    raise ValueError(f"Unsupported failure input: {path}")


def _col_letter(index: int) -> str:
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _sheet_xml(rows: list[list[str]]) -> str:
    body = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value or "")}</t></is></c>')
        body.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def write_failures_xlsx(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    failures = _load_failure_rows(Path(input_path))
    rows = [FAILURE_COLUMNS]
    for item in failures:
        rows.append([str(item.get(col, "")) for col in FAILURE_COLUMNS])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>""")
        zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>""")
        zf.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="download_failures" sheetId="1" r:id="rId1"/></sheets></workbook>""")
        zf.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>""")
        zf.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))
    return {"xlsx": str(output), "failures": len(failures)}

