---
name: session-intent-analyzer
description: "Update the per-session intent ledger for every user input and provide current intent as semantic context consumed by gates, skill-evolution, and jailbreak-detector. Keep intent summaries without storing raw prompts."
compatibility:
  - "Python 3.11+ standard library"
  - "hook-capable environments plus hookless/manual fallback"
calls:
  - "soft:jailbreak-detector"
  - "soft:skill-evolution"
---

# session-intent-analyzer

session-intent-analyzer maintains a small per-session ledger of the user's current goal, constraints, decisions, non-goals, open questions, and acceptance criteria. It lets skill-evolution interpret tool sequences with intent context and lets jailbreak-detector compare current requests against accumulated session intent.
## Contents

- [Storage Contract](#storage-contract)
- [When To Use](#when-to-use)
- [Procedure](#procedure)
- [Consumer Snapshot](#consumer-snapshot)
- [Hookless Fallback](#hookless-fallback)
- [Warnings](#warnings)


## Storage Contract

- The ledger lives under `.tmp/session-intent/<platform>/<session-id>/` in the Ghost-ALICE repo root.
- The installer passes `--root <repo>/.tmp/session-intent` to hook commands. Manual runs may override the root with `GHOST_ALICE_SESSION_INTENT_ROOT`.
- `intent-state.json` stores the latest session intent state.
- Hooks record digest-only observations and intake status. Agents add semantic deltas only when goals, constraints, decisions, or acceptance criteria materially change.
- Scalar intent fields are replaced by the latest semantic delta. Lists such as constraints, non-goals, open questions, criteria, and decisions are deduped or merged by id.
- `conduct_feedback` records compressed behavioral corrections, how the agent operated versus what the user asked, and merges by id while preserving `occurrence_count`. Repeated same-id corrections in one session increment `occurrence_count`; status-only updates do not. session-intent-analyzer captures it and skill-evolution consumes it, so the behavioral-correction loop is complete only across both skills, not in skill-evolution alone.
- `model_security_decision` is owned by jailbreak-detector. Intake preserves it and must not clobber it.
- Only a current-lineage block decision is carried to `downstream-gates.json`. Non-block decisions and non-current-lineage decisions are not carried.
- `intent-events.jsonl` records input observations and intent updates.
- `security-events.jsonl` records security judgments.
- `../current-session.json` points to the current platform session and state path.
- Raw prompts, full conversations, tool output, and secret values are never stored.
- User input is stored only as digest and length.
- Detailed schema lives in `references/ledger-schema.md`.

## When To Use

- Immediately after every user input, before task-router.
- When the user changes goals, constraints, priority, or acceptance criteria.
- When passing `--intent-ledger` to skill-evolution.
- When jailbreak-detector needs current intent context.

## Procedure

1. When the current hook supplies a `[session-intent-receipt]`, use its exact `ledger_root`, `platform`, and `session_id` as explicit `--root`, `--platform`, and `--session-id` arguments for semantic updates; use its `state_path` for downstream context. The receipt binds to the completed observation even if another session changes `current-session.json`. Do not replace it with an installed script's default root or a later pointer. Codex CLI writes are bound to the native `CODEX_THREAD_ID` when available; other hosts may supply `GHOST_ALICE_SESSION_ID`. A conflicting explicit id is rejected before writing. Without either binding, writes require an explicitly selected `--session-id`; the shared pointer is not a write identity. Hooks still resolve payload session fields before environment and pointer fallbacks for digest-only intake.
2. For hook observation, store only `input_digest`, `input_char_count`, `intake_status=observed`, and `intent_delta_status=not-provided`.
3. Add a semantic delta only when `current_goal`, `user_intent_summary`, `constraints`, `non_goals`, `decisions`, `open_questions`, or `acceptance_criteria` materially changes. When a completion, recommendation, or choice is anticipated, record verifiable `acceptance_criteria` from user intent so the final `[completion-check]` can carry them into `acceptance-criteria` and bind each claim in `claim-evidence-map`. When the user explicitly revises a boundary, record a decision naming the replaced restriction, the authorized exception, and the restrictions that still apply. Accumulating a new goal alone does not make that relationship clear.
4. Keep corrective lessons general. Do not store long episode details. Preserve the reusable reasoning pattern, not case detail. When the user corrects the agent's conduct (under-delivery, silent scope narrowing, reporting or asking instead of executing, punting a decision the content could resolve, an unrequested trace), record compressed `conduct_feedback` with a stable `id` and a reusable `summary` or `corrective_rule`. Judge correction by the asserted mismatch between a prior agent action or claim and the applicable `current_goal`, `constraints`, `non_goals`, `decisions`, or `acceptance_criteria`, not by keywords. Apply the evidence boundary below before recording or incrementing a correction. skill-evolution consumes these entries as recommendations; apply changes only when the user asks to update.
5. Use `consumer_hints` when downstream gates need immediate caution or completion criteria.
6. Use `scripts/session_intent_ledger.py` to update `intent-state.json` and `intent-events.jsonl`.
7. If security signals exist, pass `intent_summary` and `intent-state.json` to jailbreak-detector.
8. For repeated-action analysis, pass `--intent-ledger <intent-state.json>` to skill-evolution.

Correction evidence boundary:

- Use `source: user-explicit` when the user asserts a prior mismatch, directly or through a clear contextual rejection. A user's assertion is sufficient; a missing raw transcript does not negate it. Preserve the asserted mismatch without inventing additional conduct.
- Use `source: inferred` only for a gap actually observed between agent or skill behavior and the user's applicable intent. A possible future violation or the presence of buggy task code is not evidence that the agent violated the work boundary.
- New instructions, prospective prohibitions, reminders, changed requirements, and requests to refresh formerly valid results belong in the intent fields unless they also assert or reveal a prior mismatch. "Diagnose without editing" alone does not establish an unauthorized edit; "check again" alone does not establish stale-result reuse. A useful preventive rule is not automatically a correction occurrence.
- Read any supplied prior context needed to establish the mismatch before recording feedback. If the comparison remains unsupported, retain the current constraint and leave feedback absent rather than inventing a failure or asking the user to confirm routine constraints. Increment `occurrence_count` only for another supported correction observation, not for rereading a lesson or restating the same boundary.

The receipt reports a digest-only observation, not a completed semantic update or permission to write. If the bound ledger is not writable under current permissions, or a semantic update fails, report semantic persistence as failed and keep the existing authorization boundary. Do not create an alternate ledger, move historical ledgers, unset or replace host identity, or expand permissions to bypass the failure. Missing or degraded observations do not produce a receipt; do not manufacture one from conversational text. Read-only `--snapshot` with an explicit historical session id remains available for authorized inspection; it does not select the current write session.

Digest-only hook observation is enough for intake completion. Semantic delta is required only when intent materially changes.

If no delta is needed, leave `last_semantic_delta_status=not-provided` and mark `session-intent-analyzer: done`. If a delta was needed but not recorded, mark it `hook-observed`.

## Consumer Snapshot

Snapshots use snake_case `acceptance_criteria`. Final `[completion-check]` surfaces convert that to the English control field `acceptance-criteria`.

Snapshots preserve non-superseded decision bodies in `active_decisions` and the recorded `latest_scope` object, alongside the existing `decision_count`. These fields carry semantic context for interpreting corrections; they do not grant tool permission, clear a security block, retire accumulated constraints, or prove that a recorded action occurred. Absent or non-object scope becomes `{}`. Consumers must reconcile a changed decision with the preserved constraints rather than infer authority from the field's presence.

Accumulated lists can contain restrictions from earlier turns. Compare them with the current goal, scope, active decisions, and criteria before deciding the next action. Honor an explicit user revision within its stated exception; preserve the remaining restrictions. If the supplied context does not resolve a conflict, name the conflicting boundary and ask only for the missing decision while continuing uncontroversial work when possible. Do not silently downgrade an edit request to analysis, discard a restriction because a goal is newer, or require renewed approval when the existing revision already resolves the conflict.

Do not invent the chronology of a boundary. A timestamp on a decision does not date an undated restriction; active status, admitted criteria, and reports of prior edits alone do not establish that the restriction was later replaced. Distinguish a current user instruction or supplied conversation/event evidence from a compressed current_goal label. A clear current instruction can authorize a bounded change without formal revocation wording. If only conflicting snapshot fields are available, keep that conflict unresolved rather than calling one field initial or outdated without evidence. Ask about the contested authority only; missing task files are a separate issue.

```json
{
  "schema_version": "session-intent-ledger.v1",
  "current_goal": "current goal",
  "user_intent_summary": "compressed intent summary",
  "constraints": ["constraint"],
  "non_goals": ["non-goal"],
  "open_questions": ["open question"],
  "acceptance_criteria": [
    {
      "id": "criterion-id",
      "summary": "verifiable completion condition",
      "source": "user-explicit | inferred | previous-tool | system-doc"
    }
  ],
  "decision_count": 1,
  "active_decisions": [
    {"id": "current-decision", "summary": "current decision", "source": "user-explicit"}
  ],
  "latest_scope": {},
  "risk_flags": ["jailbreak-suspected"],
  "consumer_hints": {
    "skill_evolution": ["interpret tool sequence with intent context"]
  },
  "intake_status": "observed",
  "last_semantic_delta_status": "not-provided",
  "semantic_delta_policy": "agent-updates-when-intent-materially-changes"
}
```

## Hookless Fallback

Without hooks, apply the same storage contract before the first response. Use the bound host session or an explicitly selected standalone session id. If no session identity can be established, report semantic persistence as unavailable rather than guessing from the shared pointer. A deliberately isolated manual workflow may explicitly select `unknown` as degraded identity; it must not use that identity to bypass an existing host binding. Missing ledger context should fall back gracefully; it is not a denial reason by itself.

## Warnings

- This skill is not long-term memory.
- Do not promote memory without user approval.
- This skill does not make the final jailbreak decision.
- skill-evolution may use this ledger only as report context.
- Ledger text must be compressed intent, not raw quotes, secrets, system messages, or tool output.
