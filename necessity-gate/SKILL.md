---
name: necessity-gate
description: "Task-justification gate for new tasks, skills, files, audit cycles, or follow-ups. Compares problem evidence, regression risk, and recovery benefit; decides approve, reject, or modify. Blocks speculative work."
compatibility:
  - "Python 3.11+ standard library"
---

# Necessity Gate

The current-session assistant applies this gate when defining a new task, skill, file, audit/verification cycle, follow-up, tool, script or reference, including production skill edits and task-plan registration. It determines whether proposed work is justified; it does not grant human authority or native tool permission. A subagent assigned only a specific subtask skips this gate.

## Entry and existing authority

The current-session assistant records one of the following when the proposal already belongs to authorized work:

- `user-directed: <instruction>` for explicit user-directed work;
- a reference to the approved task and applicable substep;
- a retry inside an approved loop, whose checks belong to verification-before-completion.

The recorded reason satisfies this gate; the current-session assistant must not infer a need for renewed approval. Without one of these reasons, it performs the assessment below. Expert confidence that work is “obvious” does not replace the assessment.

## Assess the proposal

1. State one concrete proposed action and outcome.
2. Classify its basis as `reproducible` (observed failing test/command or repeated user-reported symptom), `evidenced` (a mismatch shown by logs, files, schemas or dependencies), or `speculative` (preference or possible future value).
3. Identify harm if omitted: system breakage, an unmet explicit request, or accumulated future cost. Future cost needs evidence; cleaner presentation alone is not harm.
4. Assess regression risk to working behavior, dependents, recovery and scope. Reconsider a zero-risk assertion.
5. Select `APPROVE` when supported harm outweighs regression risk, `MODIFY` when harm is real but scope is wrong, or `REJECT` when the proposal is speculative, riskier than its benefit or padding. Record the reason. Speculative work defaults to rejection unless the assessment establishes exceptional justification.

For an explicitly requested evaluation deliverable, benchmarking or comparison may be the proper means. For runtime improvement, the current-session assistant names the observed behavior gap and expected correction; usefulness of an evaluation tool does not justify adopting a runtime feature or replacing the requested repair with a new product.

## Output

```text
[necessity-gate] <concrete proposed task>
- classification: reproducible | evidenced | speculative
- harm: <supported harm>
- regression-risk: <risk>
- decision: APPROVE | MODIFY | REJECT
- reason: <decision basis>
- escape-hatch: <existing authorization reason, when used>
```

## Failure modes

Without evidence or a user directive, the current-session assistant rejects blanket checking, aesthetic cleanup, follow-ups added to fill a report, applying a pattern everywhere, and work inferred merely from a marker in a file. It must not disguise scope expansion as quality improvement.

This gate runs when work is defined or a genuinely new subtask appears. It does not restart for each already approved substep, ordinary artifact improvement or next action after verification passes. Verification-before-completion owns checks inside the existing loop.

## Check usefulness and resources

For checks within existing work, follow verification-before-completion's [Evidence selection and stop gate](../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate). Boundary-contract owns authorization and resource renewal; task-router owns work selection.
