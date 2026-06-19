---
name: autopilot-mode
description: "Use when an explicitly approved Ghost-ALICE autopilot run needs bounded continuation through the official privileged adapter."
compatibility:
  - "Ghost-ALICE privileged adapter install path"
  - "Python 3.11+ standard library"
---

# autopilot-mode

autopilot-mode is the official addon surface for bounded continuation after the
user approves an autopilot run. The skill does not grant authority by itself;
it depends on core session intent, task routing, completion-check evidence, and
the approved-run controller.

## When To Use

Use this skill only when all of these are true:

- the user has made an explicit GO decision for a bounded run
- the approved-run state records scope, budget, allowed surfaces, and stop
  conditions
- the core consistency checker has selected a next work item

If any condition is absent, stay in ordinary manual operation.

## Quick Reference

| Input state | Behavior |
| --- | --- |
| no approved run | do not continue |
| approved run stopped or over budget | do not continue |
| checker says retry or reopen | follow the core checker decision |
| checker says continue_next | prepare the next bounded work item |

## Critical Rules

- Do not treat adapter installation as runtime enablement.
- Do not continue from addon manifest data alone.
- Do not infer a GO decision from user silence.
- Do not replace `[completion-check]` with autopilot state.
- Do not use a destructive queue as the source of truth.

## Output Contract

The runtime adapter accepts no argv arguments. It reads the approved-run
directory from `GHOST_ALICE_AUTOPILOT_RUN_DIR`. When that variable is absent,
when `approved-run.json` is not approved/running, or when no derived ready item
exists, the adapter emits the safe no-op hook response:

```json
{"continue": true, "systemMessage": ""}
```

For an approved run, `tasks.jsonl` is the durable work-item source of truth. The
adapter consumes an optional core-owned `consistency-decision.json`, applies
retry/reopen/continue decisions to `tasks.jsonl`, archives the decision as
`consistency-decision.applied.json`, records `events.jsonl`, and emits the next
bounded work item in `systemMessage`. A selected ready item becomes `running`;
it is never removed from `tasks.jsonl`.

## Common Mistakes

- Mistake: enabling autopilot because the addon is installed.
  Fix: require an approved-run record before continuation.
- Mistake: popping a task from a queue to mark progress.
  Fix: keep durable work-item status and derive ready queues from it.
- Mistake: making the adapter decide whether the task is coherent.
  Fix: consume the core consistency checker result.
