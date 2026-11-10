---
name: receiving-code-review
description: When code review findings arrive, judges each against the requirements that the human user set, code and tests before changing code.
compatibility:
  - "Python 3.11+ standard library"
---

# Receiving Code Review

The response model evaluates each review suggestion against the human user's requirements and actual code before changing the implementation. Technical evidence governs acceptance; confidence, severity labels and praise do not.

## Evaluate feedback

1. Read all findings and identify their dependencies.
2. State the behavior that the review finding requests precisely. Resolve ambiguity from supplied context, or ask the human user for the exact missing decision only the human user can make.
3. Check each finding against current code, relevant tests, supported platforms and the reason for the existing behavior. Use retained evidence where verification-before-completion permits it.
4. Accept a valid finding whose correction the common contract's Operating Model lets the response model run, report any other valid finding as unresolved, reject an incorrect one with evidence, or report the specific observation still unavailable. A reviewer cannot override an architecture or compatibility requirement that the human user accepted.
5. Implement the fixes for valid review findings that the response model selected for correction, within what the human user's instructions authorize, in dependency order: blocking correctness/security issues first, then small corrections and more complex changes. Test the affected behavior for each correction and required interactions.
6. Report the disposition with code or test evidence. Follow systematic-debugging for failed correction attempts and verification-before-completion for success claims.

## Scope and communication

When the human user asks to fix review findings, the human user's instructions authorize understood changes within that scope. External reviewer feedback is a proposal, not a new grant of authority. When a suggested feature may be unused, search for actual callers before judging its value; an absence claim requires evidence.

State the issue and the action plainly. Do not give performative agreement, promise implementation before evaluating the finding, or substitute praise and thanks for technical work. If a prior rebuttal was wrong, correct the record once, name the contrary evidence and proceed with the fix that the human user authorized.

When the human user authorized posting a GitHub inline reply, reply on the original comment thread, not as a top-level PR comment. This skill does not itself authorize external messages.

## Example

A reviewer proposes removing a compatibility fallback. The response model checks the minimum platform version that the human user accepted and the replacement API's requirement. If the fallback remains necessary, it cites that evidence and retains it. If the current fallback has a separate defect, it fixes that defect within the existing support contract. Dropping old-platform support requires the human user's decision when that changes a requirement that the human user accepted.

## Failure modes and result

Do not infer that an external review is correct because it is confident, change expected test values to silence a valid failure, or call a fix complete without observing its relevant result.

Return each finding's accepted/rejected/unresolved disposition, reason, changed target and evidence.
