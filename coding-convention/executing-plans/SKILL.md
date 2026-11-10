---
name: executing-plans
description: Executes a plan that the response model saved under writing-plans, with the response model doing every task itself, when the human user chose inline execution.
calls:
  - "hard:finishing-a-development-branch"
  - "hard:using-git-worktrees"
compatibility:
  - "Python 3.11+ standard library"
---

# Executing Plans

The response model executes a plan that it saved under writing-plans inline, updates tasks when evidence changes the implementation, and completes the integration workflow that the human user authorized. Use subagent-driven-development when the human user authorized that execution choice and it is appropriate; tool availability alone does not replace the workflow that the human user chose.

## Procedure

1. Read the plan and its requirements. Identify missing prerequisites, contradictions and human-owned decisions before starting dependent tasks.
2. Use the checkout that the human user authorized and apply using-git-worktrees only when isolation is needed. Do not implement on main/master without the human user's explicit consent.
3. Create or update the host's task tracker from the plan. For each ready task, mark it in progress, execute its steps and required skills, obtain the specified evidence, then mark only the supported task complete.
4. When an observation contradicts the plan's order, commands, behavior or file mapping, edit the affected task and update the tracker before continuing. Keep the plan as current execution instructions, without appended attempt histories. A change to the human user's objective or a boundary the human user explicitly set requires the human user's decision; an implementation correction inside the boundary does not.
5. After all required tasks and checks satisfy the plan, invoke finishing-a-development-branch.

## Failure modes and continuation

Use systematic-debugging to investigate a failure; use boundary-contract for an unresolved authority decision.

Do not execute example commands as though they were the actual plan, add unrelated refactors, or bypass a required check. Preserve valid evidence through verification-before-completion.

## Output and integration

Report the implemented tasks, evidence for their acceptance criteria, changed plan decisions and unresolved dependencies. Verification-before-completion defines the completion and partial completion blocks and claim scope.
