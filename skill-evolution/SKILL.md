---
name: skill-evolution
description: "Use when the user asks about skill upgrades, update recommendations, or an evolution backlog; analyze io-trace and conduct_feedback to produce report-only evolution candidates. No automatic edits."
compatibility:
  - "Python 3.11+ standard library"
---

# skill-evolution

skill-evolution reads io-trace JSONL and produces evolution candidate reports
for repeated tool, workflow, and sequence patterns. If a session-intent ledger
is provided, it uses the intent summary to explain why the tool sequence
occurred.

This skill is report-only. It does not edit skills, install hooks, promote
memory, or create new automation.
## Contents

- [When To Use](#when-to-use)
- [Conduct Feedback](#conduct-feedback)
- [Procedure](#procedure)
- [Output Format](#output-format)
- [Quality Decisions](#quality-decisions)
- [Warnings](#warnings)


## When To Use

- Analyze `~/.ghost-alice/io-trace.jsonl` or isolated fixtures.
- Find repeated tool sequences in a project.
- Interpret repeated behavior with
  `.tmp/session-intent/<platform>/<session-id>/intent-state.json`.
- Answer when the user asks whether there are skill upgrades, update
  recommendations, or evolution backlog items to review.
- Gather evidence before proposing a new skill, hook, or doctor check.

## Conduct Feedback

Tool-frequency, workflow, and sequence mining finds automation candidates, but
it does not show whether the agent's behavior matched what the user asked.
`conduct_feedback` fills that gap. Read it from the session-intent ledger as a
first-class evolution signal.

Open `conduct_feedback` entries form the update-recommendation backlog.
`occurrence_count` preserves repeated same-id corrections inside one session, so
chronic completion or workflow failures do not collapse into one visible event.
A `source=user-explicit` correction or a recurring pattern is high priority.
Route each candidate through necessity-gate and name the gate skill that should
have prevented it, such as `boundary-contract` for scope failures,
`using-coding-convention` for execution discipline, or
`verification-before-completion` for false completion.

When the user asks whether there are skill upgrades, update recommendations, or
evolution backlog items, enumerate the open entries. For the full cross-session
backlog, run `scripts/aggregate_recommendations.py`. For one ledger, run
`scripts/analyze_io_trace.py --intent-ledger <intent-state.json>`, where open
entries surface as `conduct:<id>` candidates even without tool activity. This
skill stays report-only; accepted edits happen only under a separate explicit
skill-update task.

## Procedure

1. Choose the JSONL path and window size.
2. If available, add `--intent-ledger <intent-state.json>`.
3. Run `scripts/analyze_io_trace.py <path> --json [--intent-ledger <intent-state.json>]`.
4. Read `quality_summary` first and compare `review`, `watch`, and `reject`.
5. Inspect the `instincts` field, which contains evolution candidates grouped
   by tool frequency, workflow, and sequence. Treat only `quality=review`
   candidates as possible follow-up work.
6. Use `intent_context` only as supporting explanation.
7. Route `quality=review` candidates through necessity-gate before action.
8. Treat `quality=reject` and `decision=route-to-systematic-debugging` as
   debugging or noise, not skill-evolution candidates.
9. Apply any accepted change in a separate plan. Do not change skills, hooks, or
   memory automatically while this skill runs; it stays report-only.

## Output Format

```json
{
  "window": 1000,
  "event_count": 3,
  "quality_summary": {
    "review": 1,
    "watch": 0,
    "reject": 0
  },
  "instincts": [
    {
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
    }
  ]
}
```

## Quality Decisions

- `review`: Cross-session evidence suggests the pattern may generalize. Do not
  implement directly; route through necessity-gate.
- `watch`: Single-session or low-frequency evidence. Keep observing.
- `reject`: The pattern is a local test/debug loop or otherwise not a reusable
  skill evolution candidate. If `decision=route-to-systematic-debugging`, hand it
  off to the systematic-debugging loop.

## Warnings

- Default mode is report-only.
- Do not auto-evolve, auto-promote, auto-create skills, or add background hooks.
- Do not depend on personal absolute paths such as `/Users/aidall`.
- `intent_context` must come from session-intent-analyzer compressed state, not
  raw prompts.
- `confidence` is a prioritization signal, not approval evidence.
- `quality=review` is a review entry condition, not approval.
- Do not use external network, MCP, or credential surfaces.
