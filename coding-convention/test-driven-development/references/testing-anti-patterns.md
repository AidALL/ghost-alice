# Testing Anti-Patterns

Load this reference when: writing or changing tests, adding mocks, or when you want to add a test-only method to production code.

## Contents

- [Overview](#overview)
- [Iron Laws](#iron-laws)
- [Anti-Pattern 1: Testing Mock Behavior](#anti-pattern-1-testing-mock-behavior)
  - [Gate Function](#gate-function)
- [Anti-Pattern 2: Test-Only Methods in Production](#anti-pattern-2-test-only-methods-in-production)
  - [Gate Function](#gate-function-1)
- [Anti-Pattern 3: Mocking Without Understanding](#anti-pattern-3-mocking-without-understanding)
  - [Gate Function](#gate-function-2)
- [Anti-Pattern 4: Incomplete Mocks](#anti-pattern-4-incomplete-mocks)
  - [Gate Function](#gate-function-3)
- [Anti-Pattern 5: Deferred Integration Tests](#anti-pattern-5-deferred-integration-tests)
- [When Mocks Get Too Complex](#when-mocks-get-too-complex)
- [TDD Prevents These Anti-Patterns](#tdd-prevents-these-anti-patterns)
- [Quick Reference Table](#quick-reference-table)
- [Red Flags](#red-flags)
- [Conclusion](#conclusion)

## Overview

Tests must verify real behavior, not mock behavior. A mock is a means of isolation, not the thing under test.

○ Core principle: test what the code does, not what the mock does.

When you follow strict TDD, these anti-patterns do not occur.

## Iron Laws

```
1. Never test mock behavior
2. Never add a test-only method to a production class
3. Never mock a dependency you do not understand
```

## Anti-Pattern 1: Testing Mock Behavior

Violation.

```typescript
// ❌ BAD: tests that a mock exists
test('renders sidebar', () => {
  render(<Page />);
  expect(screen.getByTestId('sidebar-mock')).toBeInTheDocument();
});
```

Why it is wrong.

- It verifies that the mock works, not that the component works
- It passes when the mock is present and fails when it is absent
- It tells you nothing about real behavior

The user's correction: "Are we testing the behavior of the mock right now?"

Fix.

```typescript
// ✅ GOOD: test the real component, or do not mock it
test('renders sidebar', () => {
  render(<Page />);  // do not mock the sidebar
  expect(screen.getByRole('navigation')).toBeInTheDocument();
});

// Or, if you must mock the sidebar for isolation.
// Do not assert on the mock. Test the Page behavior with the sidebar present.
```

### Gate Function

```
Before you assert on a mock element.
  Ask: "Am I testing real component behavior, or just the existence of a mock?"

  If testing mock existence.
    Stop. Remove the assertion or unmock the component.

  Test real behavior instead.
```

## Anti-Pattern 2: Test-Only Methods in Production

Violation.

```typescript
// ❌ BAD: destroy() is used only in tests
class Session {
  async destroy() {  // looks like a production API!
    await this._workspaceManager?.destroyWorkspace(this.id);
    // ... cleanup
  }
}

// in the test
afterEach(() => session.destroy());
```

Why it is wrong.

- The production class is polluted with test-only code
- It is dangerous if it is called by mistake in production
- It violates YAGNI and separation of concerns
- It confuses the object lifecycle with the entity lifecycle

Fix.

```typescript
// ✅ GOOD: a test utility handles test cleanup
// Session has no destroy(). It is stateless in production.

// test-utils/
export async function cleanupSession(session: Session) {
  const workspace = session.getWorkspaceInfo();
  if (workspace) {
    await workspaceManager.destroyWorkspace(workspace.id);
  }
}

// in the test
afterEach(() => cleanupSession(session));
```

### Gate Function

```
Before adding a method to a production class.
  Ask: "Is this used only by tests?"

  If yes.
    Stop. Do not add it.
    Put it in a test utility.

  Ask: "Does this class own the lifecycle of this resource?"

  If no.
    Stop. This is the wrong class to put this method in.
```

## Anti-Pattern 3: Mocking Without Understanding

Violation.

```typescript
// ❌ BAD: the mock breaks the test logic
test('detects duplicate server', () => {
  // the mock blocks the config write that the test depends on!
  vi.mock('ToolCatalog', () => ({
    discoverAndCacheTools: vi.fn().mockResolvedValue(undefined)
  }));

  await addServer(config);
  await addServer(config);  // should throw, but it does not!
});
```

Why it is wrong.

- The mocked method has a side effect (the config write) that the test depends on
- Over-mocking "to be safe" breaks real behavior
- The test passes for the wrong reason or fails mysteriously

Fix.

```typescript
// ✅ GOOD: mock at the right level
test('detects duplicate server', () => {
  // mock only the slow part, preserve the behavior the test needs
  vi.mock('MCPServerManager'); // mock only the slow server startup

  await addServer(config);  // config is written
  await addServer(config);  // duplicate detected ✓
});
```

### Gate Function

```
Before you mock any method.
  Stop. Do not mock yet.

  1. Ask: "What are the side effects of the real method?"
  2. Ask: "Which of those side effects does this test depend on?"
  3. Ask: "Do I fully understand what this test needs?"

  If it depends on a side effect.
    Mock at a lower level (the real slow or external operation).
    Or use a test double that preserves the needed behavior.
    Do not mock the high-level method the test depends on.

  If you are unsure what the test needs.
    Run the test against the real implementation first.
    Observe what actually has to happen.
    Then add minimal mocking at the right level.

  Red flags.
    - "I will mock this to be safe"
    - "This might be slow, so I had better mock it"
    - Mocking without understanding the dependency chain
```

## Anti-Pattern 4: Incomplete Mocks

Violation.

```typescript
// ❌ BAD: a partial mock, only the fields you think you need
const mockResponse = {
  status: 'success',
  data: { userId: '123', name: 'Alice' }
  // missing: the metadata that downstream code uses
};

// later: it breaks when the code accesses response.metadata.requestId
```

Why it is wrong.

- A partial mock hides structural assumptions. You mock only the fields you know about.
- Downstream code may depend on a field you did not include. This is a silent failure.
- The test passes but integration fails. The mock is incomplete while the real API is complete.
- False confidence. The test proves nothing about real behavior.

Iron rule: mock the complete data structure as it exists in reality, not only the fields the immediate test uses.

Fix.

```typescript
// ✅ GOOD: mirror the real API completeness
const mockResponse = {
  status: 'success',
  data: { userId: '123', name: 'Alice' },
  metadata: { requestId: 'req-789', timestamp: 1234567890 }
  // every field the real API returns
};
```

### Gate Function

```
Before creating a mock response.
  Check: "Which fields does the real API response have?"

  Actions.
    1. Review the real API response in the docs and examples
    2. Include every field the system may consume downstream
    3. Verify that the mock fully matches the real response schema

  Important.
    If you create a mock, you must understand the entire structure.
    A partial mock fails silently when the code depends on a missing field.

  If unsure: include every documented field.
```

## Anti-Pattern 5: Deferred Integration Tests

Violation.

```
✅ implementation done
❌ tests not written
"ready for testing"
```

Why it is wrong.

- Tests are part of the implementation, not optional follow-up work
- TDD would have caught this
- You cannot claim completion without tests

Fix.

```
TDD cycle.
1. Write a failing test
2. Implement to make it pass
3. Refactor
4. Only then claim completion
```

## When Mocks Get Too Complex

Warning signs.

- The mock setup is longer than the test logic
- You mock everything just to make the test pass
- The mock omits a method the real component has
- The test breaks when the mock changes

The user's question: "Should we be using a mock here?"

Consider: an integration test with the real component is often simpler than a complex mock.

## TDD Prevents These Anti-Patterns

Why TDD helps.

1. You write the test first, which forces you to think about what you are actually testing
2. You watch it fail, which confirms the test verifies real behavior, not the mock
3. Minimal implementation, so a test-only method cannot sneak in
4. Real dependencies, so you see what the test actually needs before mocking

If you are testing mock behavior, you have violated TDD. You added a mock without watching the test fail against real code.

## Quick Reference Table

| Anti-pattern | Fix |
|----------|------|
| Asserting on a mock element | Test the real component or unmock |
| Test-only method in production | Move it to a test utility |
| Mocking without understanding | Understand the dependency first, mock minimally |
| Incomplete mock | Mirror the real API completely |
| Deferred tests | TDD: test first |
| Over-complex mock | Consider an integration test |

## Red Flags

- Asserting on a `*-mock` test ID
- A method that is called only from test files
- Mock setup is more than 50% of the test
- The test fails when the mock is removed
- Cannot explain why the mock is needed
- Mocking "just to be safe"

## Conclusion

A mock is an isolation tool, not the thing to test.

If TDD reveals that you are testing mock behavior, you have gone down the wrong path.

The fix: test real behavior, or ask why you are mocking in the first place.
