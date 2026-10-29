---
name: dispatching-parallel-agents
description: Delegates two or more tasks to parallel agents and combines their outputs, when no task edits a file another task reads or edits, no two share mutable state or an exclusive resource, and none needs another's output.
compatibility:
  - "Python 3.11+ standard library"
---

# Dispatching Parallel Agents

The parent assistant delegates independent tasks to separate agents when their work can proceed without shared mutable state or sequential dependencies. Give each agent a self-contained assignment; do not pass the whole session history.

## Decide whether to delegate

Use parallel agents for separate failures or subsystems whose investigation and edits cannot affect one another. Investigate a shared cause before dividing related failures. If agents would edit the same file, use the same exclusive resource or depend on another agent's result, assign those tasks sequentially.

The number of failures does not establish independence. For each proposed assignment, name its target files or subsystem, dependencies and permitted mutations.

## Dispatch procedure

1. Group the work by independent problem, not by arbitrary file count.
2. Prepare each assignment with the observed failure, expected result, relevant requirements, exact target paths, allowed changes, required checks and reporting format.
3. Dispatch one agent per independent assignment through the host's exposed agent API. Provide only task-relevant context and actual human authorization.
4. When results return, inspect each diff and its evidence. Identify overlapping changes before integration. Reconcile any conflict against the accepted requirements.
5. Run the integration checks required by the project and the changed shared behavior. The parent assistant follows verification-before-completion to decide whether earlier evidence remains valid and whether an additional discretionary check can change its integration decision.

## Assignment example

```text
Investigate the abort tests in src/agents/agent-tool-abort.test.ts.
Observed failure: a completed tool is reported as aborted.
Required behavior: preserve completion when completion precedes cancellation.
Allowed changes: the abort state implementation and its regression tests.
Do not edit batch-completion or approval logic; another agent owns those files.
Trace the state transition before changing code. For the known behavior, follow
test-driven-development. Replace arbitrary timing waits only when evidence
identifies timing as the cause; do not weaken an expected result to make it pass.
Return the cause, changed files, exact check results, unresolved limits and every
accessed absolute file path.
```

The example is an assignment, not executable tool syntax. Map it to the active host's tool schema.

## Failure modes

- Parallel dispatch does not authorize shared-file edits or a broader objective.
- A repeated failure needs a revised hypothesis or new evidence; follow systematic-debugging's incident limits.
- Do not increase a timeout without evidence that the original bound caused the failure.

## Result

The parent assistant reports each task's result, evidence, conflicts and remaining dependency. It then completes the requested combined result; delegation alone does not satisfy the user's objective.
