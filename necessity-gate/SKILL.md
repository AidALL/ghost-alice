---
name: necessity-gate
description: "Task-justification gate for new tasks, skills, files, audit cycles, or follow-ups. Compares problem evidence, regression risk, and recovery benefit; decides approve, reject, or modify. Blocks speculative work."
compatibility:
  - "Python 3.11+ standard library"
---

<SUBAGENT-STOP>
If this agent was dispatched to perform only a specific subtask, skip this
skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
Do not bypass this gate by expert judgment. If the work "obviously" seems
needed, still run the gate. This friction protects novice-day and bad-day agent
behavior.

Starting a new task without this gate is a rule violation.
</EXTREMELY-IMPORTANT>

# necessity-gate

necessity-gate checks whether a new task, file, audit cycle, skill change, or
follow-up is justified by real evidence and recovery value. It blocks padding,
speculative cleanup, manufactured follow-ups, and scope creep.
## Contents

- [Triggers](#triggers)
- [Escape Hatch](#escape-hatch)
- [Procedure](#procedure)
  - [1. State The Work](#1-state-the-work)
  - [2. Classify The Problem](#2-classify-the-problem)
  - [3. Assess Harm If Skipped](#3-assess-harm-if-skipped)
  - [4. Assess Regression Risk](#4-assess-regression-risk)
  - [5. Decide](#5-decide)
- [Output Format](#output-format)
- [Habitual Failure Patterns](#habitual-failure-patterns)
- [Relationship To Verification](#relationship-to-verification)
- [What This Gate Does Not Block](#what-this-gate-does-not-block)
- [References](#references)


## Triggers

Run this gate when you are about to:

- define a new task, skill, or file
- start a new audit or verification cycle
- edit frontmatter, body, or `calls` for a production skill
- add a "follow-up", "open issues", or "improvements" section to a report
- register new tasks with TodoWrite/update_plan
- create a new tool, script, or reference file
- promote an emergent subtask into real work

## Escape Hatch

The gate is considered passed only when a reason is recorded:

- `user-directed: <quote>` for explicit user instructions
- reference to a previously approved task and substep
- retry inside an already approved loop, where verification-before-completion
  absorbs the check

No silent pass is allowed.

## Procedure

### 1. State The Work

Write one concrete sentence for the proposed work. Do not use vague phrasing.

### 2. Classify The Problem

Choose exactly one:

- `reproducible`: a test fails, a command errors, or the user repeatedly reports
  the same symptom
- `evidenced`: logs, files, schemas, or dependencies show a mismatch
- `speculative`: the reason is "maybe", "cleaner", "could be useful", or
  expert preference

`speculative` defaults to reject unless later evaluation is exceptionally
strong.

### 3. Assess Harm If Skipped

Use one of these harm shapes:

- system breakage
- explicit user request unmet
- future work cost accumulation, which is always suspect and is treated together
  with speculative

"The output would look cleaner" is not harm.

### 4. Assess Regression Risk

Consider whether the work touches a working system, is relied on by other
skills or runtime paths, is hard to recover, or creates more scope expansion.
If risk is written as zero, re-evaluate; true zero is rare.

### 5. Decide

- `APPROVE`: harm is clearly larger than regression risk
- `MODIFY`: harm is real but the proposed scope is too broad or too narrow
- `REJECT`: harm is speculative, smaller than risk, or the work is padding

Record the decision and reasoning.

## Output Format

```text
[necessity-gate] <one-sentence task>
- classification: reproducible | evidenced | speculative
- harm: <one line>
- regression-risk: <one line>
- decision: APPROVE | MODIFY | REJECT
- reason: <one or two sentences>
- escape-hatch: <reason, if used>
```

## Habitual Failure Patterns

| Thought | Classification | Default |
| --- | --- | --- |
| "This is worth checking too" | speculative | reject |
| "It works, but cleanup would be nicer" | speculative cleanup | reject |
| "There are TODOs, so create a cycle" | manufactured cycle | reject (unless the TODO itself is evidence-backed) |
| "Add follow-ups to make the report complete" | padding | reject |
| "Apply the same pattern everywhere" | scope creep | reject |
| "Check everything just in case" | premature audit | reject |
| "Clean up other places at once" | batch creep | reject |
| "Make the docs look richer" | aesthetic padding | reject |

This table is not exhaustive. The threshold is that work without clear evidence
or a user directive defaults to reject.

## Relationship To Verification

necessity-gate is an initialization-event gate. It runs when work is first
defined or when a loop discovers a new subtask. Inside an already approved loop,
verification-before-completion handles the "did this step actually work"
question.

## What This Gate Does Not Block

- explicit user-directed work
- execution of a substep under already approved work
- the next step after verification passes
- improving the quality of an already approved artifact

## References

- `AGENTS.md`
- `task-router/SKILL.md`
- `coding-convention/verification-before-completion/SKILL.md`
