---
name: subagent-driven-development
description: "Executes an accepted plan in this session by giving each task, one at a time, to a new delegate and reviewing the result, when the user chose delegate execution and each task is self-contained."
compatibility:
  - "Python 3.11+ standard library"
---

# Subagent-Driven Development

The response model executes an accepted plan by giving each task to a fresh delegate, then has independent delegates review specification compliance and code quality in that order. The response model supplies a self-contained task context; delegates do not inherit the whole session history.

## Preconditions and procedure

Use this workflow when the plan's tasks can be assigned independently and execution remains in the current session. Use executing-plans for the accepted inline or separate-session workflow. Use the authorized checkout and apply using-git-worktrees only when isolation is needed; do not edit main/master without explicit user consent.

1. Read the plan once, retain every task's text and shared dependencies, and create the task tracker.
2. Select a ready task. Supply its full text, exact allowed paths, relevant context, constraints and required evidence using [references/implementer-prompt.md](references/implementer-prompt.md). The delegate need not reread the plan.
3. Answer the delegate's questions from available authority and context.
4. When implementation is ready, dispatch a delegate to review specification compliance using [references/spec-reviewer-prompt.md](references/spec-reviewer-prompt.md). That delegate checks actual code against requirements.
5. Return valid specification defects to the delegate that implemented the task. Re-review the changed condition before code-quality review. Reject incorrect findings with evidence instead of implementing them blindly.
6. After specification compliance, dispatch a delegate to review code quality using [references/code-quality-reviewer-prompt.md](references/code-quality-reviewer-prompt.md) and requesting-code-review.
7. Have the delegate that implemented the task fix valid blocking quality findings, then re-review affected changes. Mark the task complete only after each review whose outcome can change the next action or a criterion's judgment has evidence.
8. Repeat for remaining tasks. Dispatch a final review of the combined implementation, then invoke finishing-a-development-branch.

Do not have several delegates implement tasks concurrently in this workflow. For explicitly independent concurrent assignments, use dispatching-parallel-agents and its conflict boundaries.

## Model and delegate states

Choose a capable model under the host's actual options and user instructions: a fast model for a resolved mechanical task, the standard model for integration, and stronger reasoning for architecture or review. Model choice cannot compensate for missing requirements.

| Delegate state | Response model action |
| --- | --- |
| DONE | Inspect evidence and begin specification review. |
| DONE_WITH_CONCERNS | Resolve correctness or scope concerns before advancing; retain advisory observations without making them blockers. |
| NEEDS_CONTEXT | Supply the specific missing task context and resume the assignment. |
| BLOCKED | Identify the missing dependency, decision or disproven assumption. The response model supplies available context or revises the assignment within existing authority; it asks the user for a missing human-owned decision. |

A blocked delegate must not receive the same unchanged retry. Change the context, supported hypothesis, decomposition or permitted resources before resuming. Follow systematic-debugging's incident accounting; switching delegates does not reset attempts.

## Failure modes

- Do not accept self-review as either independent review.
- Do not start quality review before specification compliance.
- Do not move to the next task while a valid blocking review defect remains.
- Do not treat a delegate's review statement as proof without its inspected targets and evidence.
- Do not broaden a delegate's write scope to resolve a conflict without checking authority.
- Do not interpret delegate completion as authorization to commit, publish or message externally.

## Output and references

The response model retains task dispositions, accessed paths, actual test results and review findings. Verification-before-completion governs the final claim about the combined result. Use [references/example-workflow.md](references/example-workflow.md) for an evidence-bearing example. Delegates that implement tasks follow test-driven-development when its applicability gate passes.
