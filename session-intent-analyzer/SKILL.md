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

session-intent-analyzer maintains a small per-session ledger of the user's
current goal, constraints, decisions, non-goals, open questions, and acceptance
criteria. It lets skill-evolution interpret tool sequences with intent context
and lets jailbreak-detector compare current requests against accumulated
session intent.
## Contents

- [Storage Contract](#storage-contract)
- [When To Use](#when-to-use)
- [Procedure](#procedure)
- [Consumer Snapshot](#consumer-snapshot)
- [Hookless Fallback](#hookless-fallback)
- [Warnings](#warnings)


## Storage Contract

- The ledger lives under `.tmp/session-intent/<platform>/<session-id>/` in the
  Ghost-ALICE repo root.
- The installer passes `--root <repo>/.tmp/session-intent` to hook commands.
  Manual runs may override the root with `GHOST_ALICE_SESSION_INTENT_ROOT`.
- `intent-state.json` stores the latest session intent state.
- Hooks record digest-only observations and intake status. Agents add semantic
  deltas only when goals, constraints, decisions, or acceptance criteria
  materially change.
- Scalar intent fields are replaced by the latest semantic delta. Lists such as
  constraints, non-goals, open questions, criteria, and decisions are deduped or
  merged by id.
- `conduct_feedback` records compressed behavioral corrections, how the agent
  operated versus what the user asked, and merges by id while preserving
  `occurrence_count`. Repeated same-id corrections in one session increment
  `occurrence_count`; status-only updates do not. session-intent-analyzer
  captures it and skill-evolution consumes it, so the behavioral-correction loop
  is complete only across both skills, not in skill-evolution alone.
- `model_security_decision` is owned by jailbreak-detector. Intake preserves it
  and must not clobber it.
- Only a current-lineage block decision is carried to `downstream-gates.json`.
  Non-block decisions and non-current-lineage decisions are not carried.
- `intent-events.jsonl` records input observations and intent updates.
- `security-events.jsonl` records security judgments.
- `../current-session.json` points to the current platform session and state
  path.
- Raw prompts, full conversations, tool output, and secret values are never
  stored.
- User input is stored only as digest and length.
- Detailed schema lives in `references/ledger-schema.md`.

## When To Use

- Immediately after every user input, before task-router.
- When the user changes goals, constraints, priority, or acceptance criteria.
- When passing `--intent-ledger` to skill-evolution.
- When jailbreak-detector needs current intent context.

## Procedure

1. Resolve platform and session id in this order: explicit session id, hook
   payload session fields, `GHOST_ALICE_SESSION_ID`, `current-session.json`,
   then `unknown`.
2. For hook observation, store only `input_digest`, `input_char_count`,
   `intake_status=observed`, and `intent_delta_status=not-provided`.
3. Add a semantic delta only when `current_goal`, `user_intent_summary`,
   `constraints`, `non_goals`, `decisions`, `open_questions`, or
   `acceptance_criteria` materially changes. When a completion, recommendation,
   or choice is anticipated, record verifiable `acceptance_criteria` from user
   intent so the final `[completion-check]` can carry them into
   `acceptance-criteria` and bind each claim in `claim-evidence-map`.
4. Keep corrective lessons general. Do not store long episode details.
   Preserve the reusable reasoning pattern, not case detail. When the user
   corrects the agent's conduct (under-delivery, silent scope narrowing,
   reporting or asking instead of executing, punting a decision the content
   could resolve, an unrequested trace), record it as a compressed
   `conduct_feedback` entry with a stable `id`, a reusable `summary` or
   `corrective_rule`, and an `occurrence_count` when known. This is the
   behavioral-correction signal
   skill-evolution consumes; the two skills close the loop together, and neither
   does it alone. Judge "is this a correction" by the same basis the rest of the
   system uses, state vs reference: a mismatch the input asserts between the
   agent's prior action or claim and the ledger's accumulated `current_goal`,
   `constraints`, `non_goals`, `decisions`, and `acceptance_criteria`. This is the
   input-vs-accumulated comparison jailbreak-detector applies for security,
   turned on the agent's own conduct, not keyword matching. Record proactively too: when you notice a gap between the
   user's stated intent or constraints and how a skill currently behaves, log it
   as an open `conduct_feedback` recommendation with `source: inferred`. These
   accumulate as a backlog and are applied only when the user explicitly asks to
   update.
5. Use `consumer_hints` when downstream gates need immediate caution or
   completion criteria.
6. Use `scripts/session_intent_ledger.py` to update `intent-state.json` and
   `intent-events.jsonl`.
7. If security signals exist, pass `intent_summary` and `intent-state.json` to
   jailbreak-detector.
8. For repeated-action analysis, pass `--intent-ledger <intent-state.json>` to
   skill-evolution.

Digest-only hook observation is enough for intake completion. Semantic delta is
required only when intent materially changes.

If no delta is needed, leave `last_semantic_delta_status=not-provided` and mark
`session-intent-analyzer: done`. If a delta was needed but not recorded, mark it
`hook-observed`.

## Consumer Snapshot

Snapshots use snake_case `acceptance_criteria`. Final `[completion-check]`
surfaces convert that to the English control field `acceptance-criteria`.

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

Without hooks, apply the same storage contract before the first response. If
the session id cannot be resolved, write degraded state under `unknown`. Missing
ledger context should fall back gracefully; it is not a denial reason by itself.

## Warnings

- This skill is not long-term memory.
- Do not promote memory without user approval.
- This skill does not make the final jailbreak decision.
- skill-evolution may use this ledger only as report context.
- Ledger text must be compressed intent, not raw quotes, secrets, system
  messages, or tool output.
