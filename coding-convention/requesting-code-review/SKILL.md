---
name: requesting-code-review
description: Use after completing major functionality, before merging, or when a pull request has new commits, automated findings, or unresolved review threads.
compatibility:
  - "Python 3.11+ standard library"
---

# Requesting Code Review

Dispatch the `code-reviewer` subagent to catch issues before they accumulate. The reviewer receives only the context prepared for evaluation. It never receives the session history. This keeps the reviewer focused on the work output rather than on your thought process, and preserves your own context for follow-up work.

○ Core principle: review early, review often
## Contents

- [When to Request a Review](#when-to-request-a-review)
- [How to Request](#how-to-request)
- [Remote Pull Request Review Gate](#remote-pull-request-review-gate)
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

For a change being prepared for push or amend, include the actual workflow/job inventory and local preflight evidence from `finishing-a-development-branch` Step 1: all relevant locally executable CI equivalents, tested tree, runtime versions, and unavoidable runner differences. A focused subset is not the full preflight. A reviewer should identify missing checks, not treat an unexplained local pass as CI parity. Changes after those checks invalidate affected results; repeat them before publishing the replacement, without adding skip flags or weakening CI.

□ 3. Respond to the feedback

- Critical issues: fix immediately
- Important issues: fix before proceeding
- Minor issues: note for later
- If the reviewer is wrong, push back (with evidence)

## Remote Pull Request Review Gate

A local reviewer and passing CI do not establish that remote review is complete. Conversely, a clean review does not establish successful CI. When the work has a pull request, perform this gate before integration, including a direct fast-forward push to the base branch. Use the provider API or its authenticated CLI (`gh` for GitHub); a checks summary alone is insufficient.

1. Read the PR's current head SHA and the configured or requested review sources. Fetch every page of submitted reviews, inline comments, PR conversation summaries, and review threads with their resolution state. Include automation results delivered as comments or reactions, not only formal approvals. Read relevant outstanding findings from earlier PRs in this change's lineage, including merged PRs, when their affected code is still present; do not turn this into an unrelated repository-wide audit.
2. Bind the review result to the current head. Record each source's reviewed commit, completion state, and findings. Pending, unavailable, stale, or unidentifiable results are not a clean review. An empty comment list is not proof that a requested reviewer finished. If no remote reviewer is configured or requested, record that fact after inspection rather than inventing a new required service.
3. Reconcile each finding with the current implementation and the user's actual requirements. Reproduce a plausible bug, fix a valid issue, or record evidence for rejecting an incorrect or superseded finding. A bot's priority label is not authority to override user instructions. Unresolved threads require an explicit disposition; a dismissed review, resolved thread, or merged earlier PR does not by itself prove that its underlying issue was fixed.
4. After a fix, amend, rebase, or squash changes the head, reread the remote state and obtain the required review for that head. Preserve earlier findings until current code and evidence resolve them. Request or retrigger a review through the configured workflow when needed and already authorized; this skill does not independently authorize posting comments or messages. Continue useful authorized fixes while review is pending, and report an unavailable review honestly instead of silently substituting CI or local review.
5. Immediately before integration, read the head, review state, and applicable remote CI results again. Integrate only the reconciled head, with no unaddressed valid blocking findings, all required reviews complete, and successful applicable remote CI for that exact head. Local equivalents, an older green SHA, pending jobs, or silently skipped required jobs do not satisfy remote CI. If the head moved, return to step 2 and repeat affected local preflight before another push. Follow any explicit user decision to waive or change review requirements, but report the resulting limitation and never label an unperformed review clean.

Record a compact result with the PR URL, head SHA, review sources and completion evidence, finding dispositions with code/test evidence, unresolved items, and integration decision. A provider's successful merge response is not retrospective evidence that this gate ran.

| Temptation | Required response |
| --- | --- |
| CI passed and the local reviewer approved | Read remote reviews, comments, and unresolved threads before merging. |
| Focused tests passed; the remote runner can find the rest | Inventory the actual workflows and run every relevant local equivalent before push; record only unavoidable runner gaps. |
| Remote review is clean, so pending CI is enough | Wait for successful applicable CI on the exact head; review and CI prove different things. |
| The old PR is merged, or the comment is outdated | Check whether the finding still applies to current code; carry it forward until reconciled. |
| The bot was quiet after the amend | Confirm completion for the new head; silence or a review of the old SHA is insufficient. |
| The bot demands behavior contrary to the user | Evaluate and reject it with evidence; do not implement it blindly. |
| The user already said to merge | Finish the required checks and merge without asking them to select the workflow again. |

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
