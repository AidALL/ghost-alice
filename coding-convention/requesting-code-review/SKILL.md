---
name: requesting-code-review
description: Use after completing work, implementing major functionality, or before merge. Dispatches a code-reviewer subagent to verify the work against requirements. Review early and often.
compatibility:
  - "Python 3.11+ standard library"
---

# Requesting Code Review

Dispatch the `code-reviewer` subagent to catch issues before they accumulate.
The reviewer receives only the context prepared for evaluation. It never
receives the session history. This keeps the reviewer focused on the work
output rather than on your thought process, and preserves your own context for
follow-up work.

○ Core principle: review early, review often
## Contents

- [When to Request a Review](#when-to-request-a-review)
- [How to Request](#how-to-request)
- [Example](#example)
- [Workflow Integration](#workflow-integration)
- [Red Flags](#red-flags)


## When to Request a Review

□ Required

- After each task in subagent-driven-development
- After completing major functionality
- Before merging to main

□ Optional but valuable

- When stuck (a fresh perspective)
- Before refactoring (a baseline check)
- After fixing a complex bug

## How to Request

□ 1. Capture the git commit hashes

```bash
BASE_SHA=$(git rev-parse HEAD~1)  # or origin/main
HEAD_SHA=$(git rev-parse HEAD)
```

□ 2. Dispatch the code-reviewer subagent

Use the `Task` tool with the `code-reviewer` type. Fill in the `references/code-reviewer.md` template.

Placeholders

- `{WHAT_WAS_IMPLEMENTED}`: what you just built.
- `{PLAN_OR_REQUIREMENTS}`: what you were supposed to do.
- `{BASE_SHA}`: the starting commit.
- `{HEAD_SHA}`: the ending commit.
- `{DESCRIPTION}`: a short summary.

□ 3. Respond to the feedback

- Critical issues: fix immediately
- Important issues: fix before proceeding
- Minor issues: note for later
- If the reviewer is wrong, push back (with evidence)

## Example

```
[Task 2 complete: validation function added]

You: Request a code review before proceeding.

BASE_SHA=$(git log --oneline | grep "Task 1" | head -1 | awk '{print $1}')
HEAD_SHA=$(git rev-parse HEAD)

[dispatch code-reviewer subagent]
  WHAT_WAS_IMPLEMENTED: conversation index validation and repair functions
  PLAN_OR_REQUIREMENTS: Task 2 of .tmp/implementation-plans/deployment-plan.md
  BASE_SHA: a7981ec
  HEAD_SHA: 3df7661
  DESCRIPTION: added verifyIndex() and repairIndex(), four issue types

[subagent returns]:
  Strengths: clean architecture, real tests
  Issues:
    Important: progress indicator missing
    Minor: reporting interval magic number (100)
  Assessment: ok to proceed

You: [fix the progress indicator]
[proceed to Task 3]
```

## Workflow Integration

□ Subagent-Driven Development

- Review after each task
- Catch issues before they accumulate
- Fix before the next task

□ Executing Plans

- Review after each batch (3 tasks)
- Take the feedback, apply it, then proceed

□ Ad-Hoc Development

- Review before merge
- Review when stuck

## Red Flags

□ Never do

- Skipping review because the change "is simple"
- Ignoring Critical issues
- Proceeding with unfixed Important issues
- Arguing with valid technical feedback

□ When the reviewer is wrong

- Push back with technical evidence
- Present code and tests that prove the behavior
- Ask for clarification

Template: `references/code-reviewer.md`
