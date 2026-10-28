---
name: executing-plans
description: Use when an accepted implementation plan is ready for inline execution in the authorized checkout.
calls:
  - "hard:finishing-a-development-branch"
  - "hard:using-git-worktrees"
compatibility:
  - "Python 3.11+ standard library"
---

# Executing Plans

The implementing agent executes an accepted plan inline, updates tasks when evidence changes the implementation, and completes the authorized integration workflow. Announce use of this skill once. Use subagent-driven-development when that execution choice is authorized and appropriate; tool availability alone does not replace the chosen workflow.

## Procedure

1. Read the accepted plan and its requirements. Identify missing prerequisites, contradictions and human-owned decisions before starting dependent tasks. Resolve implementation details within existing authority; ask only for a decision the user must supply.
2. Use the authorized checkout and apply using-git-worktrees only when isolation is needed. Do not implement on main/master without explicit user consent.
3. Create or update the host's task tracker from the plan. For each ready task, mark it in progress, execute its steps and required skills, obtain the specified evidence, then mark only the supported task complete.
4. When an observation contradicts the plan's order, commands, behavior or file mapping, edit the affected task and update the tracker before continuing. Keep the plan as current execution instructions, without appended attempt histories. A change to the user's objective or explicit boundary requires that user's decision; an implementation correction inside the boundary does not.
5. After all required tasks and checks satisfy the plan, invoke finishing-a-development-branch and follow the already authorized integration choice.

## Failure modes and continuation

A missing dependency, failed check or unclear instruction stops only tasks that depend on it. Use systematic-debugging to investigate a failure; use boundary-contract for an unresolved authority decision. Continue independent authorized tasks.

Do not execute example commands as though they were the actual plan, add unrelated refactors, bypass a required check, or declare the whole plan complete from a successful subset. Preserve valid evidence through verification-before-completion; do not rerun successful task actions to correct a tracker entry.

## Output and integration

Report the implemented tasks, evidence for their acceptance criteria, changed plan decisions and unresolved dependencies. Verification-before-completion owns closure controls and claim scope.

Writing-plans supplies this workflow's input; the procedure above defines its checkout and integration handoffs.
