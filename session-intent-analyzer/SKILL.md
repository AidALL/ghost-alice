---
name: session-intent-analyzer
description: "Records the assistant's new or changed interpretation of a request before the first tool call of work, and reads the stored interpretation when it can change the objective, constraints, work choice or whether earlier work continues."
compatibility:
  - "Python 3.11+ standard library"
  - "hook-capable environments plus hookless/manual fallback"
calls:
  - "soft:skill-evolution"
---

# Session Intent Analyzer

The native intake hook records input identity without retaining raw prompt text. The common contract's Session Intent section sets when the response model records its `interpreted-as: ` interpretation; this skill sets the record's fields. Task-router selects work and skills from that record and the current request.

## Current intent and references

First identify the stored fact that can change the next decision. The supplied request and the session-start receipt can support a source lookup without another state read. Retrieve stored intent when it can affect the objective, constraints, authorized continuation, work selection or a semantic update; do not query the ledger merely because a tool will execute or a new input arrived. When the decision has no stored-state dependency and needs no semantic update, defer ledger access and proceed with the selected work. Automatic intake and strict audit still run.

For a required intent lookup, use the exact receipt coordinates with `scripts/session_intent_ledger.py --read-intent`. The result contains routing keywords, their original input binding, the current input/revision, and JSON pointers to detailed state. Keywords do not establish permission, work completion or current applicability by themselves.

The `interpreted-as: ` record is a semantic update: `current_goal` holds the objective, `constraints` and `non_goals` hold the constraints, and `acceptance_criteria` holds the done conditions, each with the source label that the common contract's Session Intent section names. The ledger stores a condition labeled `interpreted-as` as unadmitted, so it cannot be marked met; report such a condition as an assumption together with the result. When the user approves it, update that condition with `admitted: true`. The response model writes `routing_keywords` and `routing_summary` as replacements: short selection terms and a concise current objective, relevant constraints, decision and next action. Neither field contains raw prompts, tool output, full documents or repeated history. Omit an unchanged update. `constraints`, `non_goals`, `open_questions`, `risk_flags` and `consumer_hints` describe current conditions. Supply the complete current value only when it changes; the ledger replaces that field and retains omitted fields; an empty list removes obsolete entries. Preserve applicable user restrictions when preparing a replacement. A ledger edit does not authorize work.

Detailed work, authority and evidence records remain available to their owning code. When the next decision needs a stored detail, select its pointer instead of dumping the entire state:

```bash
session_intent_ledger.py --root "<receipt root>" --platform "<receipt platform>" --session-id "<receipt session>" --read-state --pointer /latest_scope --expected-input-event-id "<current input>"
```

Use `--expected-revision` when an indexed selection depends on an exact revision. The tool returns the selected value and its input/revision binding. A changed binding requires interpreting the newer input before a write.

For a semantic update, pass `--delta-json` with only changed current values and bind `--expected-input-event-id` to the current input identity that `--read-intent` returns. Code retains only the defined summary fields and required audit facts. Do not create a second state file or export merely to read current intent. The canonical SQLite API, ownership, work contract and correction details are in [references/ledger-schema.md](references/ledger-schema.md); open the relevant section only for that operation.

## Native input and authority

Codex intake records canonical input identity. Claude stages the observation until its trusted native wrapper reconciles actor rows; a notification does not become human authority. Pending input cannot authorize dependent writes. A native session binding must match the selected write session. Core reads and validates canonical state for route registration, context dispositions, authority-dependent execution and completion records; supplied prose cannot replace those checks. Historical read-only lookup does not grant that session's execution permission.

A material conduct correction can be recorded as one compressed `conduct_feedback` entry with a stable ID and supported source. User assertions support `user-said`; a directly observed mismatch supports `interpreted-as`. A prospective constraint is not evidence of an earlier failure. Task-router consumes current intent; skill-evolution receives conduct feedback only as a report branch.

## Failure modes

Do not accumulate routing keywords, obsolete summaries or resolved questions. Do not copy full state into model input. Do not infer permission from a summary, substitute another session on failure, or equate successful persistence with completed work. Missing state remains a named dependency; independent authorized work can continue.
