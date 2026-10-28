---
name: compact-handoff
description: "Use when long context, an agent transfer or session compaction could lose evidence, state and next actions; before transferring a task to another session or agent, or before requesting compaction."
compatibility:
  - "Python 3.11+ standard library"
  - "Ghost-ALICE _shared/text_lines.py, installed beside the skill"
---

# Compact Handoff

The current-session assistant uses this skill when long context, an agent transfer or session compaction could lose task state. The handoff preserves the objective, authorized boundary, evidence and next action. This skill does not trigger compaction, install hooks or create background summaries.

## Record the handoff

The current-session assistant fills the following block from retained task state and original evidence. When an authorized handoff artifact already exists, it writes the block there and invokes `python3 <loaded-skill-directory>/scripts/compact_handoff.py <absolute-handoff-file> --json` through the exposed shell tool. Otherwise it keeps the block in the host's handoff/task context and sends that same text to `python3 <loaded-skill-directory>/scripts/compact_handoff.py - --json` on stdin; do not create a report merely to obtain a filename. The validator returns `status`, `errors` and `warnings`. `status=fail` or exit 1 requires correcting the identified fields before submitting the handoff; a pass confirms only that structural check.

```text
[compact-handoff]
- objective: <current objective>
- completed: <completed work and supported scope>
- in-progress: <current item and unresolved conditions>
- next-step: <one next action>
- files-changed: <absolute paths>
- tests-run: <commands, results and original evidence source/time>
- forbidden-surface: <paths or actions not to touch>
- rollback: <nearest concrete rollback path>
```

For an unresolved repair, the current-session assistant carries the failure, affected criterion, stage, target, cumulative failed rounds, first-five reassessment status, cause hypothesis and remaining resource allowance in in-progress or next-step. It preserves original evidence and its source/time. Apply systematic-debugging's incident-accounting procedure to those transported values; the handoff is not a new incident or resource allowance.

## Pause compaction

Before requesting voluntary compaction or transferring a task, preserve the unresolved RED cause, rollback path, pending human decision and safe context the receiver needs. If a required item is missing, resolve that handoff dependency while independent authorized work continues. The native host controls automatic compaction.

## Resume

1. The sending assistant passes the validated block through the host's actual context-handoff facility, or retains it in the existing authorized task context when no explicit facility exists. This skill cannot force or defer automatic host compaction. After compaction, the receiving current-session assistant reads objective, in-progress and next-step from that supplied block. A delegated assistant receives only the context needed for its explicitly assigned subtask.
2. It compares the actual diff for files-changed with the handoff.
3. If the most recent test failed, it follows systematic-debugging for that failure.
4. Before a closure claim, it follows verification-before-completion. Unchanged valid evidence keeps its original source/time; only an affected change or evidence gap requires a new check.

## Validator and warnings

All eight fields are required. The validator rejects secret-like values without printing them. A handoff involving live config, such as platform settings/hooks or installed skill directories, requires a concrete rollback path. A tests-run value of not run or pending produces a warning: compaction may proceed, but completion is not established.

The current-session assistant must not invoke /compact automatically, create extra reports solely for compaction, hide uncertainty in the handoff or present the handoff as completion evidence. Incident accounting belongs to `../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation`; proof decisions belong to `../coding-convention/verification-before-completion/SKILL.md`.
