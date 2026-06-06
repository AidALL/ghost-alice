---
name: compact-handoff
description: "Use during long tasks when evidence, state, and next actions must survive context compaction. Handles compaction judgment and handoff records only; no automatic hook behavior."
compatibility:
  - "Python 3.11+ standard library"
---

# compact-handoff

compact-handoff preserves task continuity before or after context compaction. It
does not trigger automatic compaction, add hooks, or create background
summaries. It only defines the quality bar for a handoff that a human or future
agent can safely resume.
## Contents

- [When To Use](#when-to-use)
- [Pre-Compaction Record](#pre-compaction-record)
- [Do Not Compact When](#do-not-compact-when)
- [Resume Procedure](#resume-procedure)
- [Validator Rules](#validator-rules)
- [Warnings](#warnings)


## When To Use

- Context is long enough that work state may be lost.
- Another session or agent must continue the implementation.
- Test results, changed files, remaining work, forbidden surfaces, or rollback
  paths need to survive compaction.

## Pre-Compaction Record

Write a short handoff with these fields, then validate it with
`scripts/compact_handoff.py <handoff-file> --json`. If validation returns
`status=fail`, fix the handoff before compacting.

```text
[compact-handoff]
- objective: <current objective>
- completed: <completed work>
- in-progress: <current item>
- next-step: <one next action>
- files-changed: <absolute paths>
- tests-run: <commands and results>
- forbidden-surface: <paths or actions not to touch>
- rollback: <nearest rollback path>
```

## Do Not Compact When

- A RED test is failing and the root cause is still unknown.
- A live configuration file is being edited without a rollback path.
- The next step is a user approval gate.
- Secret, token, credential, or private key text is exposed in the context.

## Resume Procedure

1. Read `objective`, `in-progress`, and `next-step` first.
2. Inspect the real diff for `files-changed` and compare it with the handoff.
3. If the most recent test failed, route into systematic-debugging.
4. Claim completion only after verification-before-completion runs again.

## Validator Rules

- All 8 required fields must exist.
- Secret-like values fail validation. The validator must not print raw values.
- Handoffs that touch live config such as `~/.claude/settings.json`,
  `~/.codex/hooks.json`, `~/.codex/config.toml`, or `~/.agents/skills/` need a
  concrete rollback path.
- `tests-run: not run` or `pending` produces a warning. That warning does not
  block compaction, but it blocks completion claims.

## Warnings

- Do not call `/compact` automatically.
- Do not use compact-handoff as a reason to create extra reports or plans.
- Do not use compaction to hide uncertainty.
- A handoff is a continuity artifact, not evidence that the work is complete.
