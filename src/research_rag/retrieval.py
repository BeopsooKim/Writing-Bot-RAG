from __future__ import annotations

import math
import os
import time
import warnings
from collections.abc import Iterable
from typing import Any

from .config import RagConfig
from .db import connect
from .defense_route_guard import assert_not_raw_defense_query
from .embeddings import dense_search
from .query import search


DEFAULT_VERIFICATION_HINT = (
    "Query-passage relevance is not claim support; use this evidence to audit "
    "the exact claim, citation proximity, and page context before citing."
)


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def relevance_label(probability: float | None) -> str:
    if probability is None:
        return "RRF Candidate / Needs Verification"
    if probability >= 0.80:
        return "High Relevance"
    if probability >= 0.35:
        return "Medium Relevance"
    return "Low Relevance / Needs Verification"


def retrieval_confidence(label: str) -> str:
    if label == "High Relevance":
        return "high_query_relevance"
    if label == "Medium Relevance":
        return "medium_query_relevance"
    if label.startswith("Low"):
        return "low_query_relevance"
    return "candidate_needs_verification"


def verification_hint(row: dict[str, Any]) -> str:
    quality = row.get("source_quality")
    label = row.get("relevance_label")
    if quality == "metadata-only":
        return (
            "Scholar alert metadata only; use for discovery, not verified evidence. "
            + DEFAULT_VERIFICATION_HINT
        )
    if quality == "verified" and label == "High Relevance":
        return (
            "Page-grounded high query relevance, but still not automatic claim support. "
            + DEFAULT_VERIFICATION_HINT
        )
    if quality == "verified":
        return "Page-grounded candidate evidence. " + DEFAULT_VERIFICATION_HINT
    return DEFAULT_VERIFICATION_HINT


def _apply_bot_labels(
    rows: list[dict[str, Any]],
    *,
    rerank_status: str,
    rerank_error: str | None = None,
) -> list[dict[str, Any]]:
    for row in rows:
        row.setdefault("rerank_status", rerank_status)
        if rerank_error:
            row.setdefault("rerank_error", rerank_error)
        prob = row.get("rerank_score_prob")
        label = relevance_label(float(prob)) if prob is not None else relevance_label(None)
        row["relevance_label"] = label
        row["retrieval_confidence"] = retrieval_confidence(label)
        row["verification_hint"] = verification_hint(row)
    return rows


def _ranked(rows: Iterable[dict[str, Any]], rank_key: str) -> list[dict[str, Any]]:
    ranked_rows: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        copy = dict(row)
        copy[rank_key] = rank
        ranked_rows.append(copy)
    return ranked_rows


def weighted_rrf_merge(
    bm25_rows: list[dict[str, Any]],
    dense_rows: list[dict[str, Any]],
    *,
    rrf_k: int = 60,
    weight_bm25: float = 1.2,
    weight_dense: float = 1.0,
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}

    for row in _ranked(bm25_rows, "bm25_rank"):
        chunk_id = int(row["chunk_id"])
        current = dict(row)
        current["bm25_score"] = row.get("score")
        current["retrieval"] = "hybrid"
        merged[chunk_id] = current

    for row in _ranked(dense_rows, "dense_rank"):
        chunk_id = int(row["chunk_id"])
        if chunk_id not in merged:
            current = dict(row)
            current["dense_score"] = row.get("score")
            current["retrieval"] = "hybrid"
            merged[chunk_id] = current
        else:
            merged[chunk_id]["dense_rank"] = row.get("dense_rank")
            merged[chunk_id]["dense_score"] = row.get("score")

    rows: list[dict[str, Any]] = []
    for row in merged.values():
        bm25_rank = row.get("bm25_rank")
        dense_rank = row.get("dense_rank")
        score = 0.0
        if bm25_rank is not None:
            score += weight_bm25 / (rrf_k + int(bm25_rank))
        if dense_rank is not None:
            score += weight_dense / (rrf_k + int(dense_rank))
        row["weighted_rrf_score"] = score
        row["score"] = score
        rows.append(row)

    rows.sort(key=lambda item: float(item.get("weighted_rrf_score") or 0.0), reverse=True)
    return rows


def _load_chunk_texts(config: RagConfig, rows: list[dict[str, Any]]) -> dict[int, str]:
    chunk_ids = [int(row["chunk_id"]) for row in rows if row.get("chunk_id") is not None]
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" for _ in chunk_ids)
    with connect(config.db_path) as conn:
        db_rows = conn.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
    return {int(row["id"]): str(row["text"] or "") for row in db_rows}


def _as_float(value: Any) -> float:
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            value = value.tolist()
    except ImportError:
        pass
    if isinstance(value, (list, tuple)):
        if not value:
            return 0.0
        value = value[0]
    return float(value)


def _is_oom_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "out of memory" in text or "cuda" in text and "memory" in text


def _clear_cuda_cache() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


def _load_cross_encoder(model_name: str, config: RagConfig):
    hf_home = config.root / "models" / "huggingface"
    hf_home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(hf_home / "sentence-transformers"))
    local_files_only = os.environ.get("RESEARCH_RAG_LOCAL_FILES_ONLY", "1") != "0"
    if local_files_only:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    try:
        import torch
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(
            "Reranking requires torch and sentence-transformers in the RAG environment."
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*cache_dir.*deprecated.*")
        return CrossEncoder(
            model_name,
            device=device,
            cache_folder=str(hf_home / "sentence-transformers"),
            local_files_only=local_files_only,
        )


def _rerank_with_model(
    config: RagConfig,
    query: str,
    rows: list[dict[str, Any]],
    *,
    model_name: str,
    batch_size: int,
    time_budget_s: float | None,
) -> tuple[list[dict[str, Any]], str]:
    if not rows:
        return rows, "ok"

    started = time.monotonic()
    deadline = started + time_budget_s if time_budget_s and time_budget_s > 0 else None
    model = _load_cross_encoder(model_name, config)
    chunk_texts = _load_chunk_texts(config, rows)

    scored: list[tuple[int, float]] = []
    status = "ok"
    for offset in range(0, len(rows), max(1, batch_size)):
        if deadline is not None and time.monotonic() >= deadline:
            status = "partial_timeout"
            break
        batch = rows[offset : offset + max(1, batch_size)]
        pairs = [
            (
                query,
                chunk_texts.get(int(row["chunk_id"]), str(row.get("snippet") or "")),
            )
            for row in batch
        ]
        outputs = model.predict(pairs, batch_size=max(1, batch_size), show_progress_bar=False)
        for row, output in zip(batch, outputs, strict=False):
            scored.append((int(row["chunk_id"]), _as_float(output)))
        if deadline is not None and time.monotonic() >= deadline:
            status = "partial_timeout"
            break

    raw_by_chunk = {chunk_id: raw for chunk_id, raw in scored}
    reranked: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in rows:
        chunk_id = int(row["chunk_id"])
        copy = dict(row)
        if chunk_id in raw_by_chunk:
            raw = raw_by_chunk[chunk_id]
            prob = sigmoid(raw)
            copy["rerank_score_raw"] = raw
            copy["rerank_score_prob"] = prob
            copy["score"] = prob
            reranked.append(copy)
        else:
            pending.append(copy)

    reranked.sort(key=lambda item: float(item.get("rerank_score_prob") or 0.0), reverse=True)
    pending.sort(key=lambda item: float(item.get("weighted_rrf_score") or 0.0), reverse=True)
    return reranked + pending, status


def safe_rerank(
    config: RagConfig,
    query: str,
    rows: list[dict[str, Any]],
    *,
    model_name: str = "BAAI/bge-reranker-large",
    fallback_model_name: str = "BAAI/bge-reranker-base",
    candidate_k: int = 30,
    batch_size: int = 4,
    time_budget_s: float | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return rows

    candidates = rows[: max(0, candidate_k)]
    remainder = rows[max(0, candidate_k) :]
    if not candidates:
        return _apply_bot_labels(rows, rerank_status="disabled")

    try:
        reranked, status = _rerank_with_model(
            config,
            query,
            candidates,
            model_name=model_name,
            batch_size=batch_size,
            time_budget_s=time_budget_s,
        )
        final_status = status
        for row in reranked:
            row["reranker_model"] = model_name
    except Exception as exc:
        _clear_cuda_cache()
        first_error = f"{type(exc).__name__}: {exc}"
        if _is_oom_error(exc):
            first_error = f"CUDA_OOM: {exc}"
        if not fallback_model_name or fallback_model_name == model_name:
            fallback_rows = _apply_bot_labels(candidates + remainder, rerank_status="fallback_to_rrf", rerank_error=first_error)
            return fallback_rows
        try:
            reranked, status = _rerank_with_model(
                config,
                query,
                candidates,
                model_name=fallback_model_name,
                batch_size=batch_size,
                time_budget_s=time_budget_s,
            )
            final_status = "fallback_model" if status == "ok" else status
            for row in reranked:
                row["reranker_model"] = fallback_model_name
                row["rerank_error"] = first_error
        except Exception as fallback_exc:
            _clear_cuda_cache()
            error = f"{first_error}; fallback {type(fallback_exc).__name__}: {fallback_exc}"
            fallback_rows = _apply_bot_labels(candidates + remainder, rerank_status="fallback_to_rrf", rerank_error=error)
            return fallback_rows

    combined = reranked + remainder
    for row in combined:
        if "rerank_score_prob" not in row:
            row.setdefault("score", row.get("weighted_rrf_score"))
    return _apply_bot_labels(combined, rerank_status=final_status)


def retrieve(
    config: RagConfig,
    query: str,
    *,
    mode: str = "hybrid",
    top_k: int = 10,
    candidate_k: int = 50,
    rrf_k: int = 60,
    weight_bm25: float = 1.2,
    weight_dense: float = 1.0,
    dense_model: str | None = None,
    rerank: bool = False,
    reranker_model: str = "BAAI/bge-reranker-large",
    reranker_fallback_model: str = "BAAI/bge-reranker-base",
    rerank_candidate_k: int = 30,
    rerank_batch_size: int = 4,
    rerank_time_budget_s: float | None = None,
) -> list[dict[str, Any]]:
    assert_not_raw_defense_query(query, "research_rag.retrieval.retrieve")
    if mode == "bm25":
        rows = _ranked(search(config, query, top_k=top_k), "bm25_rank")
        for row in rows:
            row["retrieval"] = "bm25"
            row["bm25_score"] = row.get("score")
        return _apply_bot_labels(rows, rerank_status="disabled")

    if mode == "dense":
        rows = _ranked(dense_search(config, query, top_k=top_k, model_name=dense_model), "dense_rank")
        for row in rows:
            row["retrieval"] = "dense"
            row["dense_score"] = row.get("score")
        return _apply_bot_labels(rows, rerank_status="disabled")

    if mode != "hybrid":
        raise ValueError(f"Unsupported retrieval mode: {mode}")

    bm25_error: str | None = None
    dense_error: str | None = None
    try:
        bm25_rows = search(config, query, top_k=candidate_k)
    except Exception as exc:
        bm25_rows = []
        bm25_error = f"{type(exc).__name__}: {exc}"
    try:
        dense_rows = dense_search(config, query, top_k=candidate_k, model_name=dense_model)
    except Exception as exc:
        dense_rows = []
        dense_error = f"{type(exc).__name__}: {exc}"
    rows = weighted_rrf_merge(
        bm25_rows,
        dense_rows,
        rrf_k=rrf_k,
        weight_bm25=weight_bm25,
        weight_dense=weight_dense,
    )
    for row in rows:
        if bm25_error:
            row["bm25_error"] = bm25_error
        if dense_error:
            row["dense_error"] = dense_error

    if rerank:
        rows = safe_rerank(
            config,
            query,
            rows,
            model_name=reranker_model,
            fallback_model_name=reranker_fallback_model,
            candidate_k=rerank_candidate_k,
            batch_size=rerank_batch_size,
            time_budget_s=rerank_time_budget_s,
        )
    else:
        rows = _apply_bot_labels(rows, rerank_status="disabled")
    return rows[:top_k]
