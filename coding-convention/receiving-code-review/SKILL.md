---
name: receiving-code-review
description: When code review findings arrive, judges each against the requirements, code and tests before changing code, then fixes valid ones, rejects incorrect ones with evidence and reports the rest as unresolved.
compatibility:
  - "Python 3.11+ standard library"
---

# Receiving Code Review

The implementing agent evaluates each review suggestion against the user's requirements and actual code before changing the implementation. Technical evidence governs acceptance; confidence, severity labels and praise do not.

## Evaluate feedback

1. Read all findings and identify their dependencies.
2. State the requested behavior precisely. Resolve ambiguity from supplied context, or ask for the exact missing human decision.
3. Check each finding against current code, relevant tests, supported platforms and the reason for the existing behavior. Use retained evidence where verification-before-completion permits it.
4. Accept a valid finding, reject an incorrect one with evidence, or report the specific observation still unavailable. A reviewer cannot override a user's accepted architecture or compatibility requirement.
5. Implement valid authorized fixes in dependency order: blocking correctness/security issues first, then small corrections and more complex changes. Test the affected behavior for each correction and required interactions.
6. Report the disposition with code or test evidence. Systematic-debugging owns failed correction attempts; verification-before-completion owns success claims.

## Scope and communication

A user request to fix review findings authorizes understood changes within that scope. External reviewer feedback is a proposal, not a new grant of authority. When a suggested feature may be unused, search for actual callers before judging its value; an absence claim requires evidence.

State the issue and the action plainly. Do not give performative agreement, promise implementation before evaluating the finding, or substitute praise and thanks for technical work. If a prior rebuttal was wrong, correct the record once, name the contrary evidence and proceed with the authorized fix.

When posting a GitHub inline reply is authorized, reply on the original comment thread, not as a top-level PR comment. This skill does not itself authorize external messages.

## Example

A reviewer proposes removing a compatibility fallback. The agent checks the accepted minimum platform version and the replacement API's requirement. If the fallback remains necessary, the agent cites that evidence and retains it. If the current fallback has a separate defect, the agent fixes that defect within the existing support contract. Dropping old-platform support requires the user's decision when that changes an accepted requirement.

## Failure modes and result

Do not infer that an external review is correct because it is confident, change expected test values to silence a valid failure, or call a fix complete without observing its relevant result.

Return each finding's accepted/rejected/unresolved disposition, reason, changed target and evidence.
