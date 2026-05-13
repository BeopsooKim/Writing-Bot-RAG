from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import RagConfig
from .db import init_db
from .downloader import safe_resolve_papers
from .drive_ingest import ingest_drive_manifest
from .embeddings import build_dense_index
from .gmail_ingest import ingest_gmail_export
from .indexing import build_fts_index
from .maintenance import attach_drive_pdf, migrate_paths, remove_local_pdf_duplicate, write_failures_xlsx
from .pipeline import extract_all_pdfs, import_pdf, initialize
from .query import write_query_report
from .report import literature_review_prompt_pack
from .retrieval import retrieve
from .works import build_work_layer


def _config(args: argparse.Namespace) -> RagConfig:
    return RagConfig.from_root(getattr(args, "root", None))


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=True))


def cmd_init(args: argparse.Namespace) -> None:
    cfg = _config(args)
    path = initialize(cfg)
    _print({"status": "ok", "config": str(path), "db": str(cfg.db_path)})


def cmd_ingest_gmail(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    if not args.input:
        raise SystemExit("Provide --input JSON/JSONL exported from the Gmail connector.")
    _print(ingest_gmail_export(args.input, cfg.db_path, limit=args.limit))


def cmd_crawl_drive(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    if not args.input:
        raise SystemExit("Provide --input JSON/JSONL manifest exported from the Google Drive connector.")
    _print(ingest_drive_manifest(args.input, cfg.db_path, limit=args.limit))


def cmd_import_pdf(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    doc_id = import_pdf(cfg, args.path, title=args.title, source_family=args.family)
    _print({"document_id": doc_id})


def cmd_resolve_papers(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    _print(safe_resolve_papers(cfg, limit=args.limit, delay_s=args.delay))


def cmd_extract_pdfs(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    _print(extract_all_pdfs(cfg, limit=args.limit, force=args.force))


def cmd_build_index(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    result = build_fts_index(cfg)
    if args.dense:
        result.update(build_dense_index(cfg, model_name=args.model, batch_size=args.batch_size))
    _print(result)


def cmd_build_works(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    _print(build_work_layer(cfg.db_path, compute_hashes=not args.no_hashes))


def cmd_query(args: argparse.Namespace) -> None:
    cfg = _config(args)
    init_db(cfg.db_path)
    mode = "dense" if args.dense else args.mode
    rows = retrieve(
        cfg,
        args.query,
        mode=mode,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        rrf_k=args.rrf_k,
        weight_bm25=args.weight_bm25,
        weight_dense=args.weight_dense,
        dense_model=args.model,
        rerank=args.rerank,
        reranker_model=args.reranker_model,
        reranker_fallback_model=args.reranker_fallback_model,
        rerank_candidate_k=args.rerank_candidate_k,
        rerank_batch_size=args.rerank_batch_size,
        rerank_time_budget_s=args.rerank_time_budget_s,
    )
    report = write_query_report(cfg, args.query, rows)
    _print({"results": rows, "report": str(report)})


def cmd_report(args: argparse.Namespace) -> None:
    cfg = _config(args)
    if args.kind != "literature-review":
        raise SystemExit("Only 'literature-review' is implemented in v0.1.")
    path = literature_review_prompt_pack(cfg, args.topic, top_k=args.top_k)
    _print({"prompt_pack": str(path)})


def cmd_sync(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    _print(
        {
            "status": "ready",
            "note": "Incremental sync is state-backed. Run ingest-gmail/crawl-drive with fresh connector exports, then build-index.",
            "db": str(cfg.db_path),
        }
    )


def cmd_attach_drive_pdf(args: argparse.Namespace) -> None:
    cfg = _config(args)
    initialize(cfg)
    result = attach_drive_pdf(
        cfg.db_path,
        drive_id=args.drive_id,
        local_path=args.path,
        title=args.title,
        source_family=args.family,
    )
    if args.remove_local_duplicate:
        result.update(remove_local_pdf_duplicate(cfg.db_path, local_path=args.path))
    _print(result)


def cmd_export_failures(args: argparse.Namespace) -> None:
    _print(write_failures_xlsx(args.input, args.output))


def cmd_migrate_paths(args: argparse.Namespace) -> None:
    cfg = _config(args)
    init_db(cfg.db_path)
    _print(
        migrate_paths(
            cfg.db_path,
            old_root=args.old_root,
            new_root=args.new_root,
            apply=args.apply,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag", description="Local Research RAG CLI")
    parser.add_argument(
        "--root",
        default=None,
        help=r"RAG root directory. Defaults to $RESEARCH_RAG_ROOT, existing D:\Research_RAG, then ~/.research_rag.",
    )
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("init")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest-gmail")
    p.add_argument("--query", default=None, help="Recorded Gmail query for provenance.")
    p.add_argument("--input", required=False, help="Gmail connector JSON/JSONL export.")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_ingest_gmail)

    p = sub.add_parser("crawl-drive")
    p.add_argument("--folder-id", default=None, help="Recorded Drive folder id for provenance.")
    p.add_argument("--input", required=False, help="Drive connector JSON/JSONL manifest.")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_crawl_drive)

    p = sub.add_parser("import-pdf")
    p.add_argument("path")
    p.add_argument("--title", default=None)
    p.add_argument("--family", default=None)
    p.set_defaults(func=cmd_import_pdf)

    p = sub.add_parser("resolve-papers")
    p.add_argument("--mode", default="oa,institutional")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=2.0)
    p.set_defaults(func=cmd_resolve_papers)

    p = sub.add_parser("extract-pdfs")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--force", action="store_true", help="Re-extract already chunked PDFs.")
    p.set_defaults(func=cmd_extract_pdfs)

    p = sub.add_parser("build-index")
    p.add_argument("--dense", action="store_true", help="Also build a local GPU dense embedding index.")
    p.add_argument("--model", default="BAAI/bge-m3")
    p.add_argument("--batch-size", type=int, default=16)
    p.set_defaults(func=cmd_build_index)

    p = sub.add_parser("build-works")
    p.add_argument("--no-hashes", action="store_true", help="Do not compute SHA-256 for local PDFs.")
    p.set_defaults(func=cmd_build_works)

    p = sub.add_parser("query")
    p.add_argument("query")
    p.add_argument("--profile", default="research")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="hybrid")
    p.add_argument("--candidate-k", type=int, default=50)
    p.add_argument("--rrf-k", type=int, default=60)
    p.add_argument("--weight-bm25", type=float, default=1.2)
    p.add_argument("--weight-dense", type=float, default=1.0)
    p.add_argument("--dense", action="store_true", help="Alias for --mode dense.")
    p.add_argument("--model", default=None, help="Dense embedding model override.")
    rerank_group = p.add_mutually_exclusive_group()
    rerank_group.add_argument("--rerank", dest="rerank", action="store_true")
    rerank_group.add_argument("--no-rerank", dest="rerank", action="store_false")
    p.set_defaults(rerank=False)
    p.add_argument("--reranker-model", default="BAAI/bge-reranker-large")
    p.add_argument("--reranker-fallback-model", default="BAAI/bge-reranker-base")
    p.add_argument("--rerank-candidate-k", type=int, default=30)
    p.add_argument("--rerank-batch-size", type=int, default=4)
    p.add_argument("--rerank-time-budget-s", type=float, default=None)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("report")
    p.add_argument("kind")
    p.add_argument("--topic", required=True)
    p.add_argument("--top-k", type=int, default=20)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("sync")
    p.add_argument("--incremental", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("attach-drive-pdf")
    p.add_argument("--drive-id", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--family", default=None)
    p.add_argument("--remove-local-duplicate", action="store_true")
    p.set_defaults(func=cmd_attach_drive_pdf)

    p = sub.add_parser("export-failures-xlsx")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_export_failures)

    p = sub.add_parser("migrate-paths")
    p.add_argument("--old-root", required=True)
    p.add_argument("--new-root", required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="apply", action="store_false")
    mode.add_argument("--apply", dest="apply", action="store_true")
    p.set_defaults(apply=False, func=cmd_migrate_paths)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

