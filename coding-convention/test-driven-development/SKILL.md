---
name: test-driven-development
description: Writes a failing test first for a feature or bug fix whose expected behavior is known and automatically testable. Not for configuration, generated-output or instruction changes without a meaningful behavioral test.
compatibility:
  - "Python 3.11+ standard library"
---

# Test-Driven Development

The implementing assistant establishes a meaningful failing test for accepted behavior, implements that behavior, and observes the resulting pass. The test's expected result comes from the user's requirement and source contract.

## Applicability

Apply RED-first when the required behavior is known, its expected result can be stated independently of the proposed implementation, and an automated test can exercise the relevant production path.

If the premise, data flow, target or behavior is unknown, use bounded discovery or an implementation proof first. When that work establishes a testable production contract, apply RED-first to the subsequent implementation. Configuration, generated output and instruction changes without a meaningful behavioral test use their accepted source or runtime validation instead. Follow an explicit user instruction about implementation and verification order.

Before starting, the assistant identifies the expected failure, the production path and the independent basis for the assertion. Unknown answers require discovery under systematic-debugging or the accepted design workflow; they do not justify inventing a test.

## RED: establish the intended failure

Write a focused test with a descriptive name and an assertion about required behavior. Prefer the real production path; use a mock only for a dependency whose contract is understood. Load [testing-anti-patterns.md](references/testing-anti-patterns.md) when choosing mocks or test utilities.

Invoke the project's actual runner with the exact test target. Retain the original command, output, status and evidence locator. Judge whether the failure demonstrates the missing behavior rather than an invalid fixture, syntax error or unavailable dependency.

If the test passes, determine whether the behavior already exists or the test misses the intended condition. If the test errors, correct the fixture or prerequisite and observe the affected result. Systematic-debugging owns failed-round accounting; a diagnostic error is not automatically a failed production repair.

For applicable work, observe this intended failure before writing production code. If the current assistant prematurely wrote production code, remove only its own authorized premature change before rebuilding from the test. Preserve pre-existing user changes and retained original failure evidence.

## GREEN: satisfy the behavior

Implement the accepted behavior to the depth required by the observed cause. A structural defect may need a structural correction; passing one assertion does not settle other required criteria. Exclude unrelated features and refactors.

Run the same relevant target and inspect its result. If it fails, compare the failure with the accepted criterion: correct production behavior when it violates that criterion, or correct an assertion or fixture that misstates it. A passing implementation must not depend on weakening a valid requirement.

Use verification-before-completion's [Evidence selection and stop gate](../verification-before-completion/SKILL.md#evidence-selection-and-stop-gate) for affected additional checks and [verify-or-reuse](../verification-before-completion/references/verify-or-reuse.md) for retained results. Report unrelated failures without claiming they were caused or resolved by this change.

## REFACTOR

Once required behavior passes, simplify duplication, names or interfaces only within the accepted task. Preserve behavior and check the affected paths under the same evidence-selection procedure. Begin another RED step only for another required behavior.

## Worked example

The accepted operation makes at most three attempts and returns the successful result. This illustrative test exercises the operation through a controllable dependency:

```typescript
test('returns success on the third allowed attempt', async () => {
  let attempts = 0;
  const operation = async () => {
    attempts++;
    if (attempts < 3) throw new Error('temporary failure');
    return 'success';
  };
  expect(await retryOperation(operation)).toBe('success');
  expect(attempts).toBe(3);
});
```

The assistant first observes the intended failure in the actual project, then implements the accepted attempt limit:

```typescript
async function retryOperation<T>(operation: () => Promise<T>): Promise<T> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await operation();
    } catch (error) {
      if (attempt === 2) throw error;
    }
  }
  throw new Error('unreachable');
}
```

The example covers success on the third attempt. If the contract also requires propagating the final failure, that distinct behavior needs its own evidence. Neither sample code nor its presence in this skill proves that a test ran.

## Failure modes and result

Do not replace an unknown requirement with an implementation-shaped assertion, count mock configuration as production behavior, or treat RED-first order alone as proof of coverage. When setup is difficult, inspect the actual dependencies before prescribing interface changes or asking the user for an implementation detail.

Return the behavior covered, original RED and GREEN observations, changed production target and any unresolved requirement. Verification-before-completion owns the completion judgment, including how this evidence supports the whole requested result.
