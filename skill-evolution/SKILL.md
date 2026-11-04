---
name: skill-evolution
description: "Recommends skill changes from recorded tool-call patterns and corrections of the response model's behavior, when the user asks for such recommendations or their pending backlog, or authorizes that analysis."
compatibility:
  - "Python 3.11+ standard library"
---

# Skill Evolution

The response model uses this report-only workflow to find repeated tool and workflow patterns and supported corrections of its conduct. It reads the io-trace records and, when supplied, the intent ledger of the exact session. It returns candidate recommendations.

## Inputs and triggers

Use the workflow for a user request about skill upgrades, recommendations or an evolution backlog, or an authorized analysis of repeated tool sequences. The default trace source is `~/.ghost-alice/io-trace.jsonl`; isolated fixtures may be selected explicitly.

When the SQLite ledger exists, the analyzer reads it through the public ledger API with the supplied ledger root, host and session. The `intent-state.json` argument is a compatibility path that selects that session's ledger. A portable JSON file can support offline reports but cannot establish the current work selection or permission.

The response model reads the session's intent from the recorded current goal, the accumulated constraints and non-goals, the decisions that no later decision replaced and the latest scope. These fields explain behavior; they do not by themselves authorize execution or resolve conflicting boundaries.

## Analyze and dispose

1. The response model selects the authorized trace path and window, then uses the host's shell tool to run `python3 <loaded-skill-directory>/scripts/analyze_io_trace.py <authorized-trace-path> --window <selected-window> --json`, adding `--intent-ledger <state_path from the session metadata>` when the exact session is known. Every path is an absolute path established earlier; the `state_path` value selects that session's ledger through the storage API.
2. For each `instincts` candidate, it assesses the observed behavior or failure and its effect on the applicable contract. Tool frequency alone does not prove a conduct mismatch.
3. It then considers `quality_summary`, candidate quality and analyzer decision as triage data. Confidence, recency, authorship and artifact preservation do not grant approval.
4. It assigns every considered candidate a disposition from the closed list `absorb | project-local | session-only | reject | defer`, with an accountable owner ID in lowercase-hyphenated form, a proposed action and a rationale. Watch/reject triage candidates still receive this disposition.
5. If the disposition explicitly proposes follow-up work, that work needs its own authorization and passes necessity-gate before it runs. A defer/reject disposition grants no action. Even `decision=route-to-systematic-debugging` stays report content here; this report-only workflow does not start implementation.
6. The response model performs accepted edits only under the separately authorized editing procedure.

For a request for the full backlog across sessions, the response model runs `python3 <loaded-skill-directory>/scripts/aggregate_recommendations.py --root <authorized-ledger-root> --json`; the root comes from the ledger the user selected or from the trusted session metadata or runtime context. For one session, run the analyzer with that session's `state_path`. Open entries appear as `conduct:<id>` even when the session has no tool activity. Read the actual JSON keys and the command status before interpreting the result; the response model adds dispositions to its report, not to the stored state, and does not assume that the analyzer made those judgments of meaning. A failed or unreadable result leaves the requested coverage unresolved.

## Conduct feedback

Session-intent-analyzer defines how corrections are recorded and which source each one carries. The report reads `conduct_feedback` as a signal in its own right. The aggregator groups entries with `pattern_class` by failure family and lists their member IDs; entries without a class stay grouped by ID. The stored occurrence count records how often a supported correction repeated within a session. A `user-said` correction or a recurring pattern receives high priority.

The response model names the skill or document whose rules could prevent the behavior: for example, boundary-contract for scope, using-coding-convention for execution discipline, and verification-before-completion for false completion. It assesses the behavior and its effect on the contract before deciding whether a proposed change is justified.

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

`quality` values serve triage only: `review` indicates a possible pattern across sessions, `watch` indicates limited evidence of frequency or sessions, and `reject` indicates suspected local debugging or noise.

## Blind installed-behavior evaluation

When an authorized evaluation concerns installed behavior, follow the source-tree `docs/policies/live-smoke-regression.md` procedure. That procedure defines how the subject and the evaluator are isolated, how the runtime executes and how results are classified. Only its evaluator-confirmed behavioral failure supplies a report-only conduct-feedback candidate; a harness or transport failure does not establish misconduct by the response model.

## Warnings

The response model must not auto-evolve, auto-promote, auto-create skills or install background hooks. Intent context comes from the ledger's compressed state, never from prompt text. This workflow does not use the network, MCP or credentials, and must not depend on personal absolute paths.
