---
name: writing-plans
description: Writes an implementation plan that breaks requirements the human user accepted into tasks a newcomer to the project can carry out, after the human user settles decisions only the human user can make and the response model settles technical choices within what the human user's instructions authorize, before implementation.
calls:
  - "union:subagent-driven-development:plan-execution"
  - "union:executing-plans:plan-execution"
compatibility:
  - "Python 3.11+ standard library"
---

# Writing Plans

The response model turns requirements that the human user accepted into tasks that can be executed without prior project context. Under brainstorming, the response model asks the human user for each open design decision only the human user can make and settles technical choices within what the human user's instructions authorize. This skill does not silently settle a requirement only the human user can decide.

## Define the plan

Use the checkout that the human user authorized and apply using-git-worktrees only when isolation is needed. Save the plan by default to `.tmp/implementation-plans/YYYY-MM-DD-<feature-name>.md`; obey a location that the human user selected or the human user's prohibition on task artifacts. The plan is an execution handoff, not canonical policy. Promote policy content separately only when the human user authorized that promotion.

Map each required behavior to its files, dependencies, implementation task and verification. For several independent subsystems, define separate executable plans while preserving the overall objective and dependency order. Follow existing repository organization; propose a structural change only when it addresses the work that the human user requested.

Each task must deliver a coherent result. Divide it into executable steps, such as writing a known-contract failing test, observing its failure, implementing the behavior, observing the pass and committing when the commit policy in the human user's instructions authorizes it. Use sufficient implementation depth; do not mistake a small diff for a complete correction.

## Plan format

Start with the feature name, one-sentence goal, architecture, technologies and execution workflow that the human user selected. Use checkboxes for steps.

For every task include:

- Exact files to create, modify and test.
- Complete code for a planned code change, including referenced types and functions.
- The actual command and expected result for each check.
- Dependencies and the condition that makes the task ready.
- Acceptance criteria and any exact unresolved human decision.

Do not leave placeholder implementation text, unspecified validation, undefined calls, or instructions such as “same as Task 2” when the response model or delegate executing that task needs that code independently.

## Review and correction

Compare the whole plan with the specification that the human user accepted. Locate the task satisfying each requirement, check commands and interfaces across tasks, and remove contradictions and unsupported features. When evidence disproves a file mapping or implementation assumption, revise that task within what the human user's instructions authorize. Boundary-contract governs a change to the human user's objective or explicit constraints.

Use brainstorming's [references/document-reviewer-prompt.md](../brainstorming/references/document-reviewer-prompt.md) for an independent plan review only when it can resolve a remaining planning uncertainty. Retain valid checks after a localized correction rather than repeating the entire review without cause.

## Execution handoff

Continue the execution choice that the human user already authorized after saving or correcting the plan. When the human user's choice remains unresolved, present subagent-driven-development for task-specific delegates and executing-plans for inline execution to the human user. Invoke the skill for the workflow that the human user chose.

## Failure modes and result

The response model follows verification-before-completion before claiming that the plan satisfies its specification. The response model that later executes the plan supplies the evidence for implementation claims.
