# Code Reviewer Assignment

The parent assistant assigns this bounded review to a delegated assistant and receives its findings.

The parent assistant fills this assignment with the accepted requirements and exact review target, then sends it through the host's exposed agent API. Do not inherit the entire session history. The reviewer inspects code; the implementer's summary is context, not evidence.

## Assignment inputs

Supply the implemented behavior, accepted plan/requirements, source checkout, base and head commits, and any separately identified uncommitted diff. For work prepared for publication, include finishing-a-development-branch's workflow/job inventory, tested tree, local results and unavoidable runner gaps.

## Reviewer procedure

1. Inspect the actual diff and relevant surrounding code. For a committed range use `git diff --stat BASE..HEAD` and `git diff BASE..HEAD`, replacing both refs with the supplied validated commits. Inspect an identified uncommitted target with the appropriate working-tree diff instead.
2. Map changed behavior to the accepted requirements. Identify missing requirements, unauthorized additions and compatibility changes.
3. Review correctness, error handling, interfaces, type behavior, security and changed performance where the diff affects them.
4. Inspect whether tests exercise production behavior, required errors and integrations. Use actual test output for pass claims; a test's presence does not prove it ran.
5. Review applicable migration and documentation requirements. Judge the changed scope, not speculative future features or pre-existing size alone.
6. Return each actionable finding with file/line, trigger, consequence and evidence. Run only checks that required validation or a live review uncertainty justifies.

## Result format

Return:

- Strengths supported by inspected code or evidence.
- Critical findings: demonstrated severe breakage, security or data-loss risk.
- Important findings: required behavior, meaningful correctness or verification gaps.
- Minor findings: advisory improvements that do not block the accepted result.
- Assessment: supported readiness for this code-review stage, outstanding conditions and evidence limits.
- Every accessed absolute file path.

Use actual severity; neither soften a severe finding nor inflate a style preference. A code-review approval does not claim remote CI, remote review, installation or native delivery. Requesting-code-review owns the remote integration gate.

## Failure modes

Do not review from a summary alone, demand unrequested features, claim every edge case is covered without evidence, or infer merge readiness from a clean local diff. When evidence cannot settle a finding, state the missing observation instead of inventing a verdict.
