---
name: writing-plans
description: Writes an implementation plan that breaks accepted requirements into tasks a newcomer to the project can carry out, after design decisions are settled and before implementation.
calls:
  - "union:subagent-driven-development:plan-execution"
  - "union:executing-plans:plan-execution"
compatibility:
  - "Python 3.11+ standard library"
---

# Writing Plans

The planning agent turns accepted requirements into tasks an implementer can execute without prior project context. Announce use of this skill once. Brainstorming owns unresolved design decisions; this skill does not silently settle a human-owned requirement.

## Define the plan

Use the authorized checkout and apply using-git-worktrees only when isolation is needed. Save the plan by default to `.tmp/implementation-plans/YYYY-MM-DD-<feature-name>.md`; obey a user-selected location or prohibition on task artifacts. The plan is an execution handoff, not canonical policy. Promote policy content separately only when authorized.

Map each required behavior to its files, dependencies, implementation task and verification. For several independent subsystems, define separate executable plans while preserving the overall objective and dependency order. Follow existing repository organization; propose a structural change only when it addresses the requested work.

Each task must deliver a coherent result. Divide it into executable steps, such as writing a known-contract failing test, observing its failure, implementing the behavior, observing the pass and committing when the task's commit policy permits it. Use sufficient implementation depth; do not mistake a small diff for a complete correction.

## Plan format

Start with the feature name, one-sentence goal, architecture, technologies and selected execution workflow. Use checkboxes for steps.

For every task include:

- Exact files to create, modify and test.
- Complete code for a planned code change, including referenced types and functions.
- The actual command and expected result for each check.
- Dependencies and the condition that makes the task ready.
- Acceptance criteria and any exact unresolved human decision.

Do not leave placeholder implementation text, unspecified validation, undefined calls, or instructions such as “same as Task 2” when the implementer needs that code independently.

## Review and correction

Compare the whole plan with the accepted specification. Locate the task satisfying each requirement, check commands and interfaces across tasks, and remove contradictions and unsupported features. When evidence disproves a file mapping or implementation assumption, revise that task within actual authority. Boundary-contract governs a change to the user's objective or explicit constraints.

Use brainstorming's [references/document-reviewer-prompt.md](../brainstorming/references/document-reviewer-prompt.md) for an independent plan review only when it can resolve a remaining planning uncertainty. Retain valid checks after a localized correction rather than repeating the entire review without cause.

## Execution handoff

Continue the already authorized execution choice after saving or correcting the plan. When the choice is genuinely unresolved, present subagent-driven-development for task-specific agents and executing-plans for inline execution. Invoke the selected skill.

## Failure modes and result

The planning assistant follows verification-before-completion before claiming that the plan satisfies its specification. The assistant that later executes the plan supplies the evidence for implementation claims.
