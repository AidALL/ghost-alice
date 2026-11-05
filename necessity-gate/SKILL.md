---
name: necessity-gate
description: "Approves, rescopes or rejects new work (a task, follow-up, skill, file, tool, audit or verification cycle, or skill edit) when the response model proposes it or the user asks whether to do it."
compatibility:
  - "Python 3.11+ standard library"
---

# Necessity Gate

The response model applies this gate when defining a new task, skill, file, audit or verification cycle, follow-up, tool, script or reference, including an edit of a production skill. It decides whether the proposed work is justified. A delegate assigned only a specific subtask skips this gate.

## Entry and existing authority

The response model records one of the following when the proposal already belongs to authorized work:

- `user-directed: <instruction>` for explicit user-directed work;
- a reference to the approved task and applicable substep;
- a retry inside an approved loop; verification-before-completion selects that retry's checks.

The recorded reason satisfies this gate; the response model must not infer that new approval is needed. Without one of these reasons, it performs the assessment below. Expert confidence that work is “obvious” does not replace the assessment.

## Assess the proposal

1. State one concrete proposed action and outcome.
2. Classify its basis as `reproducible` (observed failing test/command or repeated user-reported symptom), `evidenced` (a mismatch shown by logs, files, schemas or dependencies), or `speculative` (preference or possible future value).
3. Identify the harm of omitting it: system breakage, an unmet explicit request, or accumulated future cost. Future cost needs evidence; cleaner presentation alone is not harm.
4. Assess the regression risk to working behavior, dependents, recovery and scope. Reconsider any assertion of zero risk.
5. Select `APPROVE` when supported harm outweighs regression risk, `MODIFY` when harm is real but scope is wrong, or `REJECT` when the proposal is speculative, riskier than its benefit or padding. Record the reason. Speculative work defaults to rejection unless the assessment establishes exceptional justification.

For an explicitly requested evaluation deliverable, benchmarking or comparison may be the proper means. For a runtime improvement, the response model names the observed gap in behavior and the expected correction; an evaluation tool's usefulness does not justify adopting a runtime feature or replacing the requested repair with a new product.

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

Without evidence or a user directive, the response model rejects blanket checking, aesthetic cleanup, follow-ups added to fill a report, applying a pattern everywhere, and work inferred merely from a marker in a file. It must not disguise scope expansion as quality improvement.

This gate runs when work is defined or a genuinely new subtask appears. It does not run again for an already approved substep, an ordinary improvement of an artifact or the next action after verification passes.

## Check usefulness and resources

For checks within existing work, follow verification-before-completion's [Evidence selection and stop gate](../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate). Boundary-contract defines authorization and resource renewal; task-router selects the work.
