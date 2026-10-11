---
name: session-intent-analyzer
description: "Use after every user input to maintain exact session intent and provide context for task-router and report-only skill-evolution without storing raw prompts."
compatibility:
  - "Python 3.11+ standard library"
  - "hook-capable environments plus hookless/manual fallback"
calls:
  - "soft:skill-evolution"
---

# Session Intent Analyzer

The native host invokes Core intake code. Codex records canonical digest-only input directly. Claude UserPromptSubmit stages the digest, character count and native correlation; the trusted PreToolUse or Stop wrapper reconciles that observation with native actor rows after the host appends the transcript. A human input advances canonical input authority; a task notification retains the last human input and route. Unresolved actor evidence remains pending. The current-session assistant records compressed changes to the user's goal, constraints, decisions, non-goals, questions, criteria and conduct feedback against canonical input authority, and uses that exact session for task-router or report-only skill-evolution.

## Entry and tool budget

The common First-Entry Contract determines whether this input needs normal work before agent-side tools. On that route, the current-session assistant performs the intake procedure below within the authority and resource limits owned by boundary-contract. Missing persistence remains visible; it does not change the user's authorization.

## Storage and identity

The authoritative ledger is `<ledger_root>/ghost-state.sqlite3`, keyed by exact platform and session ID. The installer supplies the repository's `.tmp/session-intent` root; an authorized manual run may explicitly set `GHOST_ALICE_SESSION_INTENT_ROOT`. Compatibility JSON/JSONL paths are import inputs or explicit exports, not runtime authority.

The current-session assistant reads through `scripts/session_intent_ledger.py --read-state` or the public API. These reads neither reconcile staged input nor mint authority. It uses the current receipt's `ledger_root`, `platform` and `session_id`; `state_path` is only a compatibility locator and may not exist. A Claude pending receipt has no `input_event_id`: its first permitted read follows the trusted wrapper's reconciliation attempt; only resolved state supplies the canonical `latest_input_event_id` for subsequent writes. Until staging is resolved, dependent semantic and route writes remain pending. A Codex canonical receipt supplies the event directly. The current-session assistant must not substitute an installed default root or `current-session.json` pointer.

Mutating Codex CLI calls bind to native `CODEX_THREAD_ID` when available; otherwise the host may bind `GHOST_ALICE_SESSION_ID`. A conflicting explicit ID fails. Without a host binding, the caller must explicitly select the session ID. Hook intake resolves payload identity before environment identity and records no guessed session when both are missing.

SQLite commits state, ordered events and advisory discovery metadata atomically. Scalars use the latest semantic delta; lists deduplicate or merge by their documented identity. The complete state, ownership, schema, mutation and route contracts are in `references/ledger-schema.md`.

## Record the current intent

1. Core records `input_digest`, `input_char_count`, `intake_status=observed` and `intent_delta_status=not-provided` for canonical human input without storing its raw text. Claude staging waits for trusted native actor reconciliation; Codex intake records directly.
2. The current-session assistant compares current intent with retained intent. It writes a delta only for a material change to `current_goal`, `user_intent_summary`, `constraints`, `non_goals`, `decisions`, `open_questions` or `acceptance_criteria`.
3. Before anticipated completion, recommendation or choice, the current-session assistant records verifiable acceptance criteria from the user intent. An explicit boundary revision records the replaced restriction, authorized exception and remaining restrictions; a newer goal label alone does not supersede a restriction.
4. The current-session assistant records supported conduct corrections under the rules below and supplies decision-relevant `consumer_hints` when needed.
5. The current-session assistant submits the compressed delta through session_intent_ledger.py using the exact receipt coordinates and canonical event as `--expected-input-event-id`. It also supplies `--expected-revision` when its judgment depends on an exact inspected revision. If a newer input invalidates the receipt, the current-session assistant interprets that input before forming a new judgment; it never attaches an old judgment to a fresh ID.
6. The current-session assistant uses the authoritative state returned by `--read-state` while applying task-router. For report-only repeated-action analysis, it invokes the skill-evolution analyzer script for the same session using `--intent-ledger <receipt.state_path>`; that reader resolves the locator through the storage API.

```bash
session_intent_ledger.py --root "<receipt.ledger_root>" --platform "<receipt.platform>" --session-id "<receipt.session_id>" --expected-input-event-id "<canonical-input-event-id>" --delta-json '{"current_goal":"<compressed current goal>"}'
```

Canonical digest observation completes intake when no semantic change is needed: retain `last_semantic_delta_status=not-provided` and report `session-intent-analyzer: done`. If a needed delta is not recorded, report `hook-observed`; unresolved staged input remains `pending`. A receipt proves observation, not semantic completion or write permission.

If a write fails, the current-session assistant reports failed persistence while preserving the existing authorization boundary. It must not create an alternate ledger, migrate history, change host identity or expand permissions to bypass the error. Missing/degraded hook observation supplies no receipt; conversational text cannot manufacture one. An explicit historical read-only snapshot does not select the current write session.

## Conduct correction evidence

The current-session assistant records a compressed reusable lesson in `conduct_feedback` with a stable ID, summary or corrective rule, source and applicable pattern class. The Python ledger writer merges by ID and preserves occurrence counts; another supported same-ID correction increments the count, while a status update or reread does not.

- A user assertion of a prior mismatch, including a clear contextual rejection, supports `source: user-explicit`. Missing raw history does not negate that assertion.
- An actually observed mismatch between agent behavior and applicable intent supports `source: inferred`. Hypothetical future misconduct or buggy task code does not prove an agent conduct failure.
- A prospective constraint, reminder, changed requirement or recheck request belongs in intent fields unless it also asserts or establishes a prior mismatch.
- When context is needed to establish an inferred comparison, the current-session assistant reads only the supplied relevant context. Unsupported comparisons remain absent; the current-session assistant does not invent a correction or ask the user to reconfirm routine constraints.
- For an objection to redundant checking, the current-session assistant evaluates recorded mutation authority and events. Hypothetical external changes do not justify the check or create another checking obligation.

The current-session assistant follows skill-evolution to interpret these lessons and write recommendations. Recording the lesson does not implement a requested correction, and a report-only recommendation does not authorize editing skills.

## Consumer interpretation

Snapshots use `acceptance_criteria`; final control output uses `acceptance-criteria`. The snapshot preserves active decision bodies in `active_decisions`, recorded `latest_scope` or `{}`, and the existing `decision_count`. These are semantic facts, not tool permission, proof of action or automatic revocation of accumulated constraints.

Before acting on conflicting retained facts, the current-session assistant follows task-router's [Boundary reconciliation](../task-router/references/routing-details.md#boundary-reconciliation). The ledger stores the facts; that procedure determines how the assistant interprets their authority and chronology.

## Hookless fallback and warnings

Without hooks, the current-session assistant uses the supplied terminal procedure for a no-work answer and claims no hook observation. For authorized normal work, it follows the same storage contract using a bound host session or explicitly selected standalone session. Without identity, it reports unavailable persistence instead of guessing from the shared pointer. A deliberately isolated manual workflow may explicitly select degraded `unknown`; it cannot override an existing host binding.

Missing ledger context alone is not a denial reason. This skill grants no native permission and is not long-term memory. The current-session assistant writes only compressed intent and defined metadata to intake events and intent-state fields; it must not put raw prompts, conversations, system/developer instructions, tool output or secrets in those records. Core response-review tables separately retain the authorized answer and selected source snapshots under the publication reference; they are not semantic intent fields. Long-term memory promotion still requires user approval. `model_security_decision` and security event logs remain compatibility audit data, not execution authorization.
