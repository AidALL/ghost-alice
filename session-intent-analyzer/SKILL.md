---
name: session-intent-analyzer
description: "Records the response model's new or changed interpretation of a request as its `interpreted-as` record before the first tool call of work, and reads that record when it can change the objective, constraints, work choice or whether earlier work continues."
compatibility:
  - "Python 3.11+ standard library"
  - "Ghost-ALICE _shared folder, installed beside the skill"
  - "hook-capable environments plus hookless/manual fallback"
calls:
  - "soft:skill-evolution"
---

# Session Intent Analyzer

Core code records the identity of each user input in the intent ledger without keeping its text. The common contract's Session Intent section sets when the response model records its `interpreted-as: ` interpretation; this skill defines the record's fields. Following task-router, the response model selects the work and skills from that record and the user's current input.

## Current intent and references

First identify the stored fact that can change the next decision. A source lookup can proceed from the user's input and the session metadata without reading the ledger. Read the stored `interpreted-as` record when it can affect the objective, the constraints, whether authorized work continues, the work selection or a semantic update (a ledger write through `--delta-json`). When the decision depends on no stored state and needs no semantic update, do not read the ledger and proceed with the selected work.

To read the stored `interpreted-as` record, run `scripts/session_intent_ledger.py --read-intent` with `ledger_root`, `platform` and `session_id` from the session metadata. The result holds the routing keywords, the input they were written for, the current input and revision, and JSON pointers to detailed state. Keywords alone do not establish that the work is complete or that the keywords still apply.

The response model writes the `interpreted-as: ` record as a semantic update: `current_goal` holds the objective, `constraints` and `non_goals` hold the constraints, and `acceptance_criteria` holds the acceptance criteria, each with the source label that the common contract's Session Intent section names. The ledger stores an acceptance criterion labeled `interpreted-as` as unadmitted, so it cannot be marked met; report such an acceptance criterion as an assumption together with the result. When the user approves it, update that acceptance criterion with `admitted: true`. `routing_keywords` holds short terms for selecting work and `routing_summary` holds a concise current objective, the relevant constraints, the decision and the next action; each write replaces the earlier value. Neither field contains prompt text, tool output, full documents or repeated history. Send no update when nothing changed. `constraints`, `non_goals`, `open_questions`, `risk_flags` and `consumer_hints` hold current values. Supply a field's complete current value only when it changes, and an empty list to remove all of its entries. Keep the applicable user restrictions when preparing a replacement.

The ledger also holds detailed work, authority and evidence records for the Core code that uses them. When the next decision needs one stored detail, read it by its JSON pointer:

```bash
session_intent_ledger.py --root "<ledger_root>" --platform "<platform>" --session-id "<session_id>" --read-state --pointer /latest_scope --expected-input-event-id "<input_event_id>"
```

Pass `--expected-revision` when a pointer with a list index depends on an exact revision. The command returns the selected value with the input and revision it belongs to. When that input or revision has changed, interpret the newer input before writing.

For a semantic update, pass `--delta-json` with only the changed values and pass `--expected-input-event-id` with the current input ID that `--read-intent` returns. Core code keeps only the defined summary fields and the event records it requires. Do not create a second state file or an export only to read the current `interpreted-as` record. The canonical SQLite API, ownership, work contract and correction details are in [references/ledger-schema.md](references/ledger-schema.md); open the relevant section only for that operation.

## Input records and authority

[Canonical intake provenance](references/ledger-schema.md#canonical-intake-provenance) defines how Core code records input on each host; a notification never counts as human authority. A pending input cannot authorize the writes that depend on it. The session that the host reports must match the session selected for a write. Core code reads and validates the ledger state for response route registration, context dispositions, execution that depends on authority, and completion records; supplied prose cannot replace those checks. A read-only lookup of an earlier session does not grant that session's permission to execute.

The response model can record a material correction of its conduct as one compressed `conduct_feedback` entry with a stable ID and a supported source. An assertion by the user supports the source `user-said`; a mismatch the response model observed directly supports `interpreted-as`. A constraint for future work is not evidence of an earlier failure. Task-router uses the current `interpreted-as` record; skill-evolution reads `conduct_feedback` only for its reports.

## Failure modes

Do not accumulate routing keywords, obsolete summaries or resolved questions. A successful ledger write does not establish that the work is complete. Name missing state as an unresolved dependency.
