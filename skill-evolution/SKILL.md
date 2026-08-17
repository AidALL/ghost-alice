---
name: skill-evolution
description: "Use when the user asks about skill upgrades, update recommendations, or an evolution backlog; analyze io-trace and conduct_feedback to produce report-only evolution candidates. No automatic edits."
compatibility:
  - "Python 3.11+ standard library"
---

# skill-evolution

skill-evolution reads io-trace JSONL and produces evolution candidate reports for repeated tool, workflow, and sequence patterns. If a session-intent ledger is provided, it uses the intent summary to explain why the tool sequence occurred.

This skill is report-only. It does not edit skills, install hooks, promote memory, or create new automation.
## Contents

- [When To Use](#when-to-use)
- [Conduct Feedback](#conduct-feedback)
- [Blind Installed-Behavior Evaluation](#blind-installed-behavior-evaluation)
- [Procedure](#procedure)
- [Output Format](#output-format)
- [Quality Decisions](#quality-decisions)
- [Warnings](#warnings)


## When To Use

- Analyze `~/.ghost-alice/io-trace.jsonl` or isolated fixtures.
- Find repeated tool sequences in a project.
- Interpret repeated behavior with `.tmp/session-intent/<platform>/<session-id>/intent-state.json`.
- Answer when the user asks whether there are skill upgrades, update recommendations, or evolution backlog items to review.
- Gather evidence before proposing a new skill, hook, or doctor check.

## Conduct Feedback

Tool-frequency, workflow, and sequence mining finds automation candidates, but it does not show whether the agent's behavior matched what the user asked. `conduct_feedback` fills that gap. Read it from the session-intent ledger as a first-class evolution signal.

Open `conduct_feedback` entries form the update-recommendation backlog. `occurrence_count` preserves repeated same-id corrections inside one session, so chronic completion or workflow failures do not collapse into one visible event. A `source=user-explicit` correction or a recurring pattern is high priority. Every candidate first receives a behavior-first assessment of its observed behavior or failure mode and contract impact, then a closed disposition. Only a disposition that proposes follow-up work routes to necessity-gate. Name the likely preventing gate skill as the candidate's owner when it owns the behavior, such as `boundary-contract` for scope failures, `using-coding-convention` for execution discipline, or `verification-before-completion` for false completion.

When the user asks whether there are skill upgrades, update recommendations, or evolution backlog items, enumerate the open entries. For the full cross-session backlog, run `scripts/aggregate_recommendations.py`. For one ledger, run `scripts/analyze_io_trace.py --intent-ledger <intent-state.json>`, where open entries surface as `conduct:<id>` candidates even without tool activity. This skill stays report-only; accepted edits happen only under a separate explicit skill-update task.

## Blind Installed-Behavior Evaluation

Skill pressure RED/GREEN tests whether the written methodology changes behavior under disclosed pressure; it is not installed-behavior release evidence. Post-install blind behavior evaluation uses a held-out case in a fresh subject session. The subject sees only the authentic prompt and ordinary runtime context. The evaluation purpose, rubric, expected answer, pass criteria, prior output, and experiment label remain evaluator-private. Only an evaluator-confirmed behavioral failure emits exactly one report-only conduct-feedback candidate; it never auto-edits a skill or writes recommendation state. Harness, transport, provenance, timeout, empty-output, or malformed-evaluator failures emit no conduct-feedback candidate. Detailed blind-evaluation transport, provenance, and persistence rules are owned by the source-tree live-smoke policy and blind-behavior runtime.

## Procedure

1. Choose the JSONL path and window size.
2. If available, add `--intent-ledger <intent-state.json>`.
3. Run `scripts/analyze_io_trace.py <path> --json [--intent-ledger <intent-state.json>]`.
4. Inspect the `instincts` field. For each candidate, assess the observed behavior or failure mode, then its contract impact.
5. Use `intent_context` only as supporting explanation.
6. After behavior and impact are established, use `quality_summary` and each candidate's quality as quality triage evidence. Quality is triage evidence, never eligibility or approval authority. `instincts[*].decision` and quality are analyzer triage data only.
7. Give every considered candidate a closed disposition: `absorb`, `project-local`, `session-only`, `reject`, or `defer`. Record its `owner`, `action`, and `rationale`. `dispositions[*].disposition` is a report-only classification and routing recommendation. necessity-gate remains the execution authority for any proposed skill, file, task, or follow-up work. `owner` is the accountable contract or skill identifier, written as a lowercase hyphenated id; it is not a human role. Items triaged as `watch` or `reject` still receive a closed disposition. A closed disposition does not make a candidate an implementation candidate.
8. A `defer` or `reject` disposition authorizes no action. Route through necessity-gate before execution only when the recorded disposition and action explicitly propose follow-up work. Analyzer `decision` does not authorize a handoff.
9. Record `decision=route-to-systematic-debugging` as triage context and assign a closed disposition; do not hand off unless that disposition authorizes it.
10. Apply any accepted change in a separate plan. Do not change skills, hooks, or memory automatically while this skill runs; it stays report-only.

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
  ],
  "dispositions": [
    {
      "candidate_id": "sequence:read-edit-bash",
      "disposition": "defer",
      "owner": "skill-evolution",
      "action": "none",
      "rationale": "observed contract impact is not yet established"
    }
  ]
}
```

## Quality Decisions

- Select candidates by observed behavior or failure mode and contract impact before consulting quality triage.
- Recency, authorship, and artifact preservation are not approval authority.
- `quality=review` is explicitly non-approving.
- Every considered candidate must receive a closed disposition of `absorb`, `project-local`, `session-only`, `reject`, or `defer`, with an `owner`, `action`, and `rationale`.
- `review`: Cross-session evidence suggests the pattern may generalize; this is triage evidence only.
- `watch`: Single-session or low-frequency evidence; this is triage evidence only.
- `reject`: The analyzer suspects a local test/debug loop or noise; this is triage evidence only.

## Warnings

- Default mode is report-only.
- Do not auto-evolve, auto-promote, auto-create skills, or add background hooks.
- Do not depend on personal absolute paths such as `/Users/aidall`.
- `intent_context` must come from session-intent-analyzer compressed state, not raw prompts.
- `confidence` is a prioritization signal, not approval evidence.
- `quality=review` is a review entry condition, not approval.
- Do not use external network, MCP, or credential surfaces.
