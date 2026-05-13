# Local RAG-Backed Writing Mode

Use this reference when writing or revision needs source support, citation checking, related-work evidence, claim-evidence alignment, or source-safe paraphrasing from `<RAG_ROOT>`.

## Query Wrapper

```powershell
python "skills/writing-bot/scripts/run_local_rag_query.py" --query "<claim or paragraph topic>" --top-k 10
```

Prefer workspace-based audit when Research Bot has created a handoff:

```powershell
python "skills/writing-bot/scripts/rag_writing_audit.py" --workspace "<workspace path or latest>" --claim "<claim>"
```

Use `evidence_pack.json` as the compact source of truth. Do not paste full chunks into the prompt. Treat `semantic_excerpt_not_localized` as a warning that the dense-only excerpt is a semantic lead, not a localized support sentence.

The RAG output may include `work_id`, `canonical_key`, `source_quality`, `duplicate_sources`, `page_range`, and `local_path`. Prefer evidence with PDF page provenance over metadata-only alerts.

Default wrapper behavior is hybrid retrieval: BM25 + dense search are fused with weighted RRF, then a cross-encoder reranker is attempted within the timeout budget. If reranking times out or fails, the wrapper retries once with `--mode hybrid --no-rerank` and returns a JSON payload rather than blocking the Bot.

RAG output may also include `bm25_rank`, `dense_rank`, `weighted_rrf_score`, `rerank_score_raw`, `rerank_score_prob`, `relevance_label`, `retrieval_confidence`, `rerank_status`, and `verification_hint`. Treat `relevance_label` as query-passage relevance only; it is not citation support or claim support.

## Evidence Status

Use these labels exactly:

- `verified`: exact metadata or source content was checked directly from a PDF/source page or authoritative external source.
- `rag-supported`: local RAG retrieved relevant chunks with document/page provenance, but wording still needs claim-boundary review.
- `metadata-only`: only Scholar alert or bibliographic metadata is available; no source content has been checked.
- `manuscript-only`: judgment is based only on the user's draft or supplied text.
- `unverifiable`: the source or claim cannot currently be checked with confidence.

Never use a retrieved adjacent source to launder a citation into supporting a stronger claim.

## Mandatory Warnings

If `source_quality=metadata-only`, classify the item as `Discovery lead only` and print this exact warning:

**경고: 이 결과는 metadata-only입니다. 본문 PDF/page를 확인할 수 없어 이 claim의 인용 근거로 보장할 수 없습니다.**

If `rerank_status=disabled`, `partial_timeout`, or `fallback_to_rrf`, print this exact warning near the claim-evidence map:

**주의: reranker 검증이 완료되지 않은 후보 근거입니다. claim support 여부를 별도로 확인해야 합니다.**

Few-shot rule:

- `High Relevance + metadata-only` -> citation prohibited until the source body/page is checked.
- `High/Medium Relevance + verified page` -> candidate evidence; still audit whether the exact claim is supported.
- `fallback_to_rrf` -> retrieval candidate only; never call it citation support.

## Workflow

1. Extract major external claims before polishing prose.
2. Query local RAG for claims that need source support or field positioning.
3. Build a claim-evidence map before rewriting high-stakes academic text.
4. Revise wording to match the safe claim boundary of the evidence.
5. Preserve user authorship: provide local rewrites, alternatives, and evidence-aware edits rather than full ghostwritten documents.
6. Mark missing citations, unsupported claims, and overextended source use explicitly.

## Output Additions

When local RAG is used, include:

- `Claim-evidence map`
- `Citation-needed claims`
- `Unsupported or overextended claims`
- `Safe revision strategy`

## Reviewer Hell Defense

When a workspace contains `critique_pack.json`, use the defense workflow instead of free-form rebuttal writing:

```powershell
python "skills/writing-bot/scripts/rag_student_defense.py" --workspace "<workspace path or latest>"
python "skills/writing-bot/scripts/rag_advisor_shield.py" --workspace "<workspace path or latest>"
```

If a critique requires new evidence, attach the human-produced artifact with a researcher memo before defense:

```powershell
python "skills/writing-bot/scripts/rag_attach_artifact.py" --workspace "<workspace>" --critique-id C001 --file "<result.csv>" --artifact-type simulation_result --researcher-memo "<what this artifact shows, does not show, and what claim boundary it supports>"
```

Rules:

- A file path alone is not evidence. `researcher_memo` is required before an artifact can move beyond `blocked`.
- Optional metric fields are sanity checks only. Missing unit, missing threshold source, missing baseline for an improvement claim, or ambiguous threshold direction must keep the item at `needs_human_judgment`.
- Use only `usable_for_draft`, `needs_human_judgment`, and `blocked`; never call a Reviewer Hell output verified or submission-ready.
- Advisor Shield should choose `defend`, `narrow`, or `concede`. `blocked` items belong in researcher action items, not final rebuttal prose.


