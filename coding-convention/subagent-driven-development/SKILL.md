---
name: subagent-driven-development
description: "Executes an accepted plan of independent tasks in the current session, with a fresh implementer for each task."
compatibility:
  - "Python 3.11+ standard library"
---

# Subagent-Driven Development

The parent assistant executes an accepted plan through a fresh implementer for each task, then independent specification and code-quality reviews in that order. The parent assistant supplies a self-contained task context; subagents do not inherit the whole session history.

## Preconditions and procedure

Use this workflow when the plan's tasks can be assigned independently and execution remains in the current session. Use executing-plans for the accepted inline or separate-session workflow. Use the authorized checkout and apply using-git-worktrees only when isolation is needed; do not edit main/master without explicit user consent.

1. Read the plan once, retain every task's text and shared dependencies, and create the task tracker.
2. Select a ready task. Supply its full text, exact allowed paths, relevant context, constraints and required evidence using [references/implementer-prompt.md](references/implementer-prompt.md). The implementer need not reread the plan.
3. Answer the implementer's questions from available authority and context. A missing human-owned decision pauses only its dependent work.
4. When implementation is ready, dispatch the specification reviewer using [references/spec-reviewer-prompt.md](references/spec-reviewer-prompt.md). That reviewer checks actual code against requirements; the implementer's report is not proof.
5. Return valid specification defects to the implementer. Re-review the changed condition before code-quality review. Reject incorrect findings with evidence instead of implementing them blindly.
6. After specification compliance, dispatch the code-quality reviewer using [references/code-quality-reviewer-prompt.md](references/code-quality-reviewer-prompt.md) and requesting-code-review.
7. Have the implementer fix valid blocking quality findings, then re-review affected changes. Mark the task complete only after each review whose outcome can change the next action or a criterion's judgment has evidence.
8. Repeat for remaining tasks. Dispatch a final review of the combined implementation, then invoke finishing-a-development-branch.

Do not run multiple implementers concurrently in this workflow. For explicitly independent concurrent assignments, use dispatching-parallel-agents and its conflict boundaries.

## Model and implementer states

Choose a capable model under the host's actual options and user instructions: a fast model for a resolved mechanical task, the standard model for integration, and stronger reasoning for architecture or review. Model choice cannot compensate for missing requirements.

| Implementer state | Parent assistant action |
| --- | --- |
| DONE | Inspect evidence and begin specification review. |
| DONE_WITH_CONCERNS | Resolve correctness or scope concerns before advancing; retain advisory observations without making them blockers. |
| NEEDS_CONTEXT | Supply the specific missing task context and resume the assignment. |
| BLOCKED | Identify the missing dependency, decision or disproven assumption. The parent assistant supplies available context or revises the assignment within existing authority; it asks the user for a missing human-owned decision. |

A blocked agent must not receive the same unchanged retry. Change the context, supported hypothesis, decomposition or permitted resources before resuming. Follow systematic-debugging's incident accounting; switching agents does not reset attempts.

## Failure modes

- Do not accept self-review as either independent review.
- Do not start quality review before specification compliance.
- Do not move to the next task while a valid blocking review defect remains.
- Do not treat a reviewer statement as proof without its inspected targets and evidence.
- Do not broaden a subagent's write scope to resolve a conflict without checking authority.
- Do not interpret subagent completion as authorization to commit, publish or message externally.

## Output and references

The parent assistant retains task dispositions, accessed paths, actual test results and review findings. Verification-before-completion governs the final claim about the combined result. Use [references/example-workflow.md](references/example-workflow.md) for an evidence-bearing example. Implementers follow test-driven-development when its applicability gate passes.
