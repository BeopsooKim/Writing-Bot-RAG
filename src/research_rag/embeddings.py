from __future__ import annotations

import json
import os
from pathlib import Path

from .config import RagConfig
from .db import connect, ensure_work_schema
from .query import enrich_results


def _normalize(vectors):
    import numpy as np

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vectors / norms


def build_dense_index(
    config: RagConfig,
    model_name: str = "BAAI/bge-m3",
    batch_size: int = 16,
) -> dict[str, object]:
    hf_home = config.root / "models" / "huggingface"
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(hf_home / "sentence-transformers"))
    local_files_only = os.environ.get("RESEARCH_RAG_LOCAL_FILES_ONLY", "1") != "0"
    if local_files_only:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Dense indexing requires numpy, torch, and sentence-transformers. "
            "Install requirements-gpu.txt in the RAG Conda environment."
        ) from exc

    with connect(config.db_path) as conn:
        rows = conn.execute("SELECT id, text FROM chunks ORDER BY id").fetchall()
    if not rows:
        return {"dense_chunks_indexed": 0, "model": model_name, "device": "none"}

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(
        model_name,
        device=device,
        cache_folder=str(hf_home / "sentence-transformers"),
        local_files_only=local_files_only,
    )
    texts = [row["text"] for row in rows]
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=False,
        show_progress_bar=True,
    )
    vectors = _normalize(vectors).astype("float32")
    config.index_dir.mkdir(parents=True, exist_ok=True)
    np.save(config.index_dir / "dense_embeddings.npy", vectors)
    (config.index_dir / "dense_chunk_ids.json").write_text(
        json.dumps([int(row["id"]) for row in rows], ensure_ascii=False),
        encoding="utf-8",
    )
    (config.index_dir / "dense_config.json").write_text(
        json.dumps({"model": model_name, "device": device, "chunks": len(rows)}, indent=2),
        encoding="utf-8",
    )
    return {"dense_chunks_indexed": len(rows), "model": model_name, "device": device}


def dense_search(
    config: RagConfig,
    query: str,
    top_k: int = 10,
    model_name: str | None = None,
) -> list[dict[str, object]]:
    hf_home = config.root / "models" / "huggingface"
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(hf_home / "sentence-transformers"))
    local_files_only = os.environ.get("RESEARCH_RAG_LOCAL_FILES_ONLY", "1") != "0"
    if local_files_only:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Dense search requires numpy, torch, and sentence-transformers. "
            "Install requirements-gpu.txt in the RAG Conda environment."
        ) from exc

    embeddings_path = config.index_dir / "dense_embeddings.npy"
    chunk_ids_path = config.index_dir / "dense_chunk_ids.json"
    dense_config_path = config.index_dir / "dense_config.json"
    if not embeddings_path.exists() or not chunk_ids_path.exists():
        return []

    dense_config = {}
    if dense_config_path.exists():
        dense_config = json.loads(dense_config_path.read_text(encoding="utf-8"))
    selected_model = model_name or dense_config.get("model") or "BAAI/bge-m3"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(
        selected_model,
        device=device,
        cache_folder=str(hf_home / "sentence-transformers"),
        local_files_only=local_files_only,
    )
    query_vec = model.encode([query], convert_to_numpy=True, normalize_embeddings=False)
    query_vec = _normalize(query_vec).astype("float32")[0]
    vectors = np.load(embeddings_path)
    scores = vectors @ query_vec
    chunk_ids = json.loads(chunk_ids_path.read_text(encoding="utf-8"))
    top_idx = np.argsort(-scores)[:top_k]
    selected = [(int(chunk_ids[i]), float(scores[i])) for i in top_idx]
    if not selected:
        return []

    placeholders = ",".join("?" for _ in selected)
    score_map = {chunk_id: score for chunk_id, score in selected}
    with connect(config.db_path) as conn:
        ensure_work_schema(conn)
        rows = conn.execute(
            f"""
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
              substr(c.text, 1, 500) AS snippet
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            LEFT JOIN works w ON w.id = d.work_id
            WHERE c.id IN ({placeholders})
            """,
            [chunk_id for chunk_id, _ in selected],
        ).fetchall()
    by_id = {int(row["chunk_id"]): dict(row) for row in rows}
    results: list[dict[str, object]] = []
    for chunk_id, score in selected:
        row = by_id.get(chunk_id)
        if row:
            row["score"] = score
            row["retrieval"] = "dense"
            results.append(row)
    return enrich_results(config, results)

