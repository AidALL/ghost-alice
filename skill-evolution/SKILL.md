---
name: skill-evolution
description: "Recommends skill changes from recorded tool-call patterns and corrections of the assistant's behavior, when the user asks for such recommendations or their pending backlog, or authorizes that analysis."
compatibility:
  - "Python 3.11+ standard library"
---

# Skill Evolution

The current-session assistant uses this report-only workflow to identify repeated tool/workflow patterns and supported conduct corrections. It reads io-trace and, when supplied, the exact session-intent ledger. It returns candidate recommendations.

## Inputs and triggers

Use the workflow for a user request about skill upgrades, recommendations or an evolution backlog, or an authorized analysis of repeated tool sequences. The default trace source is `~/.ghost-alice/io-trace.jsonl`; isolated fixtures may be selected explicitly.

When SQLite authority exists, the reader uses the public ledger API with the supplied root/platform/session. The `intent-state.json` argument is a compatibility locator. Portable JSON can support offline reporting but cannot establish current routing or permission.

The current-session assistant interprets intent through the retained current goal, accumulated constraints/non-goals, active non-superseded decisions and latest scope. These fields explain behavior; they do not authorize execution or resolve conflicting boundaries by themselves.

## Analyze and dispose

1. The current-session assistant selects the authorized trace path and window, then uses the exposed shell tool to run `python3 <loaded-skill-directory>/scripts/analyze_io_trace.py <authorized-trace-path> --window <selected-window> --json`, adding `--intent-ledger <receipt.state_path>` when exact session context is available. Every path is an established absolute path; the compatibility locator selects the receipt's ledger through the storage API.
2. For each `instincts` candidate, it assesses the observed behavior or failure and its effect on the applicable contract. Tool frequency alone does not prove a conduct mismatch.
3. It then considers `quality_summary`, candidate quality and analyzer decision as triage data. Confidence, recency, authorship and artifact preservation do not grant approval.
4. It assigns every considered candidate a closed disposition: `absorb | project-local | session-only | reject | defer`, with accountable lowercase-hyphenated owner ID, proposed action and rationale. Watch/reject triage candidates still receive this disposition.
5. If the disposition explicitly proposes follow-up work, that separately authorized task passes necessity-gate before execution. A defer/reject disposition grants no action. Even `decision=route-to-systematic-debugging` remains report context here; the report-only branch does not dispatch implementation.
6. The current-session assistant performs accepted edits only under the separately authorized editing procedure.

For a full cross-session backlog request, the current-session assistant runs `python3 <loaded-skill-directory>/scripts/aggregate_recommendations.py --root <authorized-ledger-root> --json`; the root comes from the user's selected ledger or trusted receipt/runtime context. For one session, use the analyzer with that exact intent locator. Open entries appear as `conduct:<id>` even without tool activity. Read the actual JSON keys and command status before interpretation; the assistant adds dispositions to its report, not to source state, and does not assume the analyzer generated those meaning judgments. A failed or unreadable result leaves the requested coverage unresolved.

## Conduct feedback

Session-intent-analyzer owns capture and provenance of corrections. The report reads `conduct_feedback` as a first-class signal. The aggregator groups entries with `pattern_class` by failure family and includes their member IDs; unclassified entries remain grouped by ID. The stored occurrence count preserves repeated supported corrections within a session. A user-said correction or recurring pattern receives high priority.

The current-session assistant names the contract owner that could prevent the behavior: for example, boundary-contract for scope, using-coding-convention for execution discipline, and verification-before-completion for false completion. It assesses behavior and contract impact before deciding whether a proposed change is justified.

## Output

```json
{
  "window": 1000,
  "event_count": 3,
  "quality_summary": {"review": 1, "watch": 0, "reject": 0},
  "instincts": [{
    "id": "sequence:read-edit-bash",
    "trigger": "Read -> Edit -> Bash sequence",
    "confidence": 0.7,
    "domain": "sequence",
    "scope": "project",
    "evidence": "3 occurrences",
    "quality": "review",
    "decision": "necessity-gate",
    "session_count": 2,
    "quality_reasons": ["cross-session-evidence"],
    "intent_context": {
      "current_goal": "session intent guard implementation",
      "constraints": ["do not store raw prompts"],
      "decision_count": 1
    }
  }],
  "dispositions": [{
    "candidate_id": "sequence:read-edit-bash",
    "disposition": "defer",
    "owner": "skill-evolution",
    "action": "none",
    "rationale": "observed contract impact is not yet established"
  }]
}
```

Quality values have only triage meaning: review indicates possible cross-session generality, watch indicates limited frequency/session evidence, and reject indicates suspected local debugging or noise.

## Blind installed-behavior evaluation

When an authorized evaluation concerns installed behavior, follow the source-tree `docs/policies/live-smoke-regression.md` procedure. It owns subject/evaluator isolation, runtime execution and result classification. Only its evaluator-confirmed behavioral failure supplies a report-only conduct-feedback candidate; a harness or transport failure does not establish agent misconduct.

## Warnings

The current-session assistant must not auto-evolve, auto-promote, auto-create skills or install background hooks. Intent context comes from compressed ledger state, never raw prompts. This workflow does not use network, MCP or credential surfaces, and must not depend on personal absolute paths.
