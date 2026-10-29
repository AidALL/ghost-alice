---
name: using-coding-convention
description: Before each step of development work (file read, command, edit, plan or review), loads the coding-convention skill that fits it, such as writing-plans or test-driven-development.
compatibility:
  - "Python 3.11+ standard library"
---

# Using Coding Convention

The current-session assistant starting development uses this entrypoint to select the applicable family skills. A delegated assistant assigned one bounded task follows the parent assistant's supplied workflow instead of routing the whole conversation again; when the user's own direct input arrives after that handoff, it follows that input wherever it differs from the parent's workflow or interpretation.

## Priority and loading

User instructions and project rules govern the task; family skills govern the implementation procedure within that authority. Load or validly reuse the family skill selected for the next development action. When uncertain applicability would change that action, read the body and decide; otherwise continue with the established workflow.

Use the host's actual skill mechanism:

- Claude Code: invoke the visible Skill tool and follow the returned body.
- Codex: read or validly reuse the body, then perform its workflow for the current input. Metadata and a prior verdict cannot substitute for that workflow.
- Copilot CLI: use its exposed skill capability and [references/copilot-tools.md](references/copilot-tools.md).
- Other hosts: map operations to the exposed tool schemas; do not invent unavailable tools.

For Codex operations, delegated-task boundaries and checkout selection, use [references/codex-tools.md](references/codex-tools.md). Instruction body reuse is defined in verification-before-completion's [Instruction Body Reuse](../verification-before-completion/references/verify-or-reuse.md#instruction-body-reuse). On a new input, the current-session assistant applies the workflow to that input; it need not reread a valid retained body.

## Workflow

After routing selects development work, the current-session assistant uses the table below to choose its family workflow. The common contract's Session Intent and Work Selection sections own the earlier interpretation and carry task-router's direct-answer rule.

Before the next file read, command, edit, plan, review or development-status claim, select the applicable workflow below. Reuse accepted design and authorization. If evidence contradicts the plan, revise the implicated assumption or task within that authorization. Follow boundary-contract to distinguish an implementation change within existing authority from a change requiring the user’s decision.

| Condition | Workflow |
| --- | --- |
| Design or expected behavior remains unresolved | brainstorming |
| Multi-step requirements are known | writing-plans |
| A failure needs a cause and a correction | systematic-debugging |
| Known behavior can be exercised by a meaningful test | test-driven-development |
| Independent tasks can run concurrently | dispatching-parallel-agents |
| An accepted plan will run through task-specific subagents | subagent-driven-development |
| An accepted plan will run inline | executing-plans |
| The task needs an isolated checkout | using-git-worktrees |
| Implementation needs review | requesting-code-review |
| A reviewer proposes changes | receiving-code-review |
| A skill or its references need authoring | writing-skills |
| The branch is ready for its authorized integration choice | finishing-a-development-branch |
| A new completion, fix, success, verified-status or material recommendation claim, or rereading an artifact to support a claim | verification-before-completion |

Use the selected skill's body for its procedure, required calls and output format. Do not infer its procedure from this table.

## Final claims

Verification-before-completion owns evidence reuse, completion and partial-control formats, and the finalization sequence.

For repair attempts, follow systematic-debugging's [Incident accounting and continuation](../systematic-debugging/SKILL.md#incident-accounting-and-continuation). For authorization, follow boundary-contract. Do not copy either procedure into a task-specific plan.

## Failure modes

- Familiarity with a skill name does not satisfy body loading.
- A small change does not excuse skipping an applicable required workflow.
- Tool availability does not grant authority to use the tool.
- An agent's plan cannot weaken the user's acceptance criteria.
- A passed diagnostic or report cannot complete an implementation request.
- Repeating a check requires the evidence trigger defined by verification-before-completion.

Write prose paragraphs on one physical source line unless Markdown, syntax or protocol fields require separate lines.
