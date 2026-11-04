---
name: compact-handoff
description: "Writes a handoff of the task's objective, scope, evidence and next action when a long conversation, compaction or handover to another session or agent could lose them, and resumes the task from it afterwards."
compatibility:
  - "Python 3.11+ standard library"
  - "Ghost-ALICE _shared/text_lines.py, installed beside the skill"
---

# Compact Handoff

The response model uses this skill when a long conversation, a transfer to another agent or session compaction could lose the state of the task. This skill does not trigger compaction, install hooks or create background summaries.

## Record the handoff

The response model fills the following block from the retained task state and the original evidence. When an authorized handoff file already exists, it writes the block there and invokes `python3 <loaded-skill-directory>/scripts/compact_handoff.py <absolute-handoff-file> --json` through the host's shell tool. Otherwise it keeps the block in the host's handoff or task context and sends that same text to `python3 <loaded-skill-directory>/scripts/compact_handoff.py - --json` on stdin; it does not create a report only to obtain a filename. The validator returns `status`, `errors` and `warnings`. On `status=fail` or exit 1, the response model corrects the named fields before submitting the handoff; a pass confirms only the block's structure.

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

For an unresolved repair, the response model carries the failure, the affected acceptance criterion, the stage, the target, the cumulative failed rounds, the status of the reassessment after five failed rounds, the cause hypothesis and any stopping condition in `in-progress` or `next-step`. It keeps the original evidence with its source and time. It applies systematic-debugging's incident accounting to those values; the handoff does not create a new incident.

## Pause compaction

Before requesting compaction or transferring a task, the response model records the unresolved cause of a failing test, the rollback path and the context the receiver needs, without secrets. If a required item is missing, the response model obtains it before the handoff.

## Resume

1. The response model that hands off the task passes the validated block through the host's context-handoff facility, or keeps it in the existing authorized task context when the host has no such facility. After compaction, the response model that resumes the task reads `objective`, `in-progress` and `next-step` from that block. A delegate receives only the context its assigned subtask needs.
2. It compares the actual diff of the files in `files-changed` with the handoff.
3. If the most recent test failed, it follows systematic-debugging for that failure.
4. Before claiming completion or partial completion, it follows verification-before-completion.

## Validator and warnings

All eight fields are required. The validator rejects secret-like values without printing them. A handoff that involves live configuration, such as host settings and hooks or installed skill directories, requires a concrete rollback path. A `tests-run` value of `not run` or `pending` produces a warning: compaction may proceed, but completion is not established.

The response model must not invoke /compact automatically, create extra reports only for compaction or present the handoff as completion evidence. `../coding-convention/verification-before-completion/SKILL.md` decides what proves completion.
