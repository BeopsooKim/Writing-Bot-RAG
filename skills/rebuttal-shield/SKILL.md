---
name: rebuttal-shield
description: Use this skill when the user wants to defend against a Reviewer Hell critique_pack.json, attach researcher memos or simulation artifacts, prepare a rebuttal draft, generate an advisor-style shield, or convert reviewer attacks into defend/narrow/concede revision actions. Requires a local RAG workspace and preserves the v6 minimal gate states usable_for_draft, needs_human_judgment, and blocked.
---

# Rebuttal Shield

Turn a `critique_pack.json` into a researcher-led rebuttal draft and revision plan. This is a thin alias for the Writing Bot Reviewer Hell defense path.

## Attach Artifact or Memo

```powershell
python "skills/writing-bot/scripts/rag_attach_artifact.py" `
  --workspace "<workspace path or latest>" `
  --critique-id C001 `
  --file "<artifact path>" `
  --artifact-type simulation_result `
  --researcher-memo "<what this artifact shows, does not show, and what claim boundary it supports>" `
  --root "<RAG_ROOT>"
```

Optional metric sanity fields:

```powershell
--metric settling_time --metric-value 0.05 --unit s --baseline-value 0.08 --threshold 0.1 --threshold-source advisor --threshold-direction le
```

Metric fields are not an oracle. Missing unit, threshold source, baseline for improvement claims, or threshold direction keeps the item at `needs_human_judgment`.

## Draft Defense

```powershell
python "skills/writing-bot/scripts/rag_student_defense.py" `
  --workspace "<workspace path or latest>" `
  --root "<RAG_ROOT>"
```

```powershell
python "skills/writing-bot/scripts/rag_advisor_shield.py" `
  --workspace "<workspace path or latest>" `
  --root "<RAG_ROOT>"
```

## Outputs

- `rebuttal_draft.json`
- `student_defense.md`
- `claim_boundary_map.json`
- `advisor_shield.json`
- `final_rebuttal_letter.md`
- `revision_plan.md`

Blocked items belong in researcher action items, not final rebuttal prose.


