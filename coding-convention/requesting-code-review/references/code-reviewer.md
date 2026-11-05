# Code Review Assignment

The parent assigns this bounded review to a delegate and receives its findings.

The parent fills this assignment with the accepted requirements and exact review target, then sends it through the host's exposed agent API. The delegate inspects the changed artifacts; the implementation summary is context, not evidence.

## Assignment inputs

Supply the implemented behavior, accepted plan/requirements, artifact types and the exact review target established by requesting-code-review's [How to Request](../SKILL.md#how-to-request) procedure. Identify the checkout, base and head commits, and any separately identified uncommitted diff. For work prepared for publication, include finishing-a-development-branch's workflow/job inventory, tested tree, local results and unavoidable runner gaps.

Supply accessible original-source locators for the relevant requirements and known corrections, separately from the parent's current interpretation and assumptions. The locators identify the applicable revision and passage, field or record when needed to distinguish the source. For retrieval and later handoffs, the delegate reads `references/context-selection.md` in the installed task-router folder. The delegate retrieves the original passages needed for its review; when it cannot access a required locator, the parent resolves access or supplies only the necessary excerpt with its source. The delegate reports a conflict with the original constraint or a missing required source to the parent, with the affected requirement and evidence limit.

## Evidence by artifact

For executable changes, inspect the implementation, relevant callers and tests. Use actual execution results for runtime claims; source inspection supports only the behavior it can establish.

For instruction and procedure changes, apply writing-skills' [operational handoff guidance](../../writing-skills/SKILL.md#author-the-package) to the changed text, required references and callers. Compare the accepted requirements and original constraints with what the recipient is instructed to do. Source conformance and an observed action of the recipient are different evidence stages.

For a mixed change, apply each method to its affected artifacts and identify which evidence supports each finding.

## Review procedure

1. Inspect the actual diff and relevant surrounding artifacts at the supplied checkout. For a committed range use `git diff --stat BASE..HEAD` and `git diff BASE..HEAD`, replacing both refs with the supplied validated commits. Inspect an identified uncommitted target with the appropriate working-tree diff instead.
2. Map changed behavior to the accepted requirements. Identify missing requirements, unauthorized additions and compatibility changes.
3. Review correctness, error handling, interfaces, type behavior, security and changed performance where the diff affects them.
4. Select evidence under [Evidence by artifact](#evidence-by-artifact). For executable changes, inspect whether tests exercise production behavior, required errors and integrations; a test's presence does not prove it ran.
5. Review applicable migration and documentation requirements. Judge the changed scope, not speculative future features or pre-existing size alone.
6. Return each actionable finding with file/line, trigger, consequence and evidence.

## Result format

Return:

- Critical findings: demonstrated severe breakage, security or data-loss risk.
- Important findings: required behavior, meaningful correctness or verification gaps.
- Minor findings: advisory improvements that do not block the accepted result.
- Assessment: supported readiness for this code-review stage, outstanding conditions and evidence limits.
- Every accessed absolute file path.

Use actual severity; neither soften a severe finding nor inflate a style preference. A code-review approval does not claim remote CI, remote review, installation or delivery in the host. Requesting-code-review defines the remote integration gate.

## Failure modes

Do not review from a summary alone, demand unrequested features, claim every edge case is covered without evidence, or infer merge readiness from a clean local diff. When evidence cannot settle a finding, state the missing observation instead of inventing a verdict.
