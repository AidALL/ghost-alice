# Testing Anti-Patterns

The implementing assistant uses this reference when selecting mocks or test utilities for work admitted by the main skill's applicability gate. Each example concerns a concrete production behavior; mock setup and test order alone do not prove that behavior.

## Assertions about mock presence

An assertion that a placeholder mock exists measures the fixture rather than the required interface:

```typescript
// This proves only that the substituted sidebar marker is rendered.
render(<Page />);
expect(screen.getByTestId('sidebar-mock')).toBeInTheDocument();
```

When navigation is the requirement, render the real component and assert its navigation interface:

```typescript
render(<Page />);
expect(screen.getByRole('navigation')).toBeInTheDocument();
```

If isolation requires substituting the sidebar, assert the Page behavior that the substitution permits testing. Do not describe that observation as evidence for the real sidebar.

## Test-only methods in production

Before adding a production method, establish its actual production callers and the class's responsibility. Test cleanup belongs in a test utility when the production class does not own that resource lifecycle.

```typescript
// A test utility owns cleanup for this fixture.
export async function cleanupSession(session: Session) {
  const workspace = session.getWorkspaceInfo();
  if (workspace) {
    await workspaceManager.destroyWorkspace(workspace.id);
  }
}
afterEach(() => cleanupSession(session));
```

This example assumes the accepted Session API already exposes workspace information. It does not authorize adding a production API solely for tests.

## Mocking a required side effect

Inspect a dependency's actual inputs, outputs and side effects before substituting it. If duplicate detection depends on a configuration write, mocking the method that performs that write removes a precondition of the test.

Substitute the external or slow boundary while preserving the configuration operation the criterion requires. If that dependency contract is unknown, inspect the real path or authoritative interface first. An unexpected pass or failure is a reason to trace the path, not add more mocks indiscriminately.

## Incomplete response contracts

Obtain the dependency's response schema from its authoritative source. Include the fields consumed by the exercised downstream path and the optional or absent cases relevant to the accepted requirement.

```typescript
const response = {
  status: 'success',
  data: { userId: '123', name: 'Alice' },
  metadata: { requestId: 'req-789', timestamp: 1234567890 }
};
```

This illustrative fixture includes metadata because the tested consumer requires it. Do not infer a smaller contract from the immediate assertion or claim that this example lists every field of an actual API.

## Choosing integration evidence

When mocks reproduce most of a dependency or obscure the causal behavior, compare the cost and evidentiary value of exercising the real component in an isolated integration check. Choose the method that can distinguish the actual alternatives within the task's resources.

Required tests and their order follow the main [TDD applicability and cycle](../SKILL.md). Instruction or configuration edits without a meaningful behavioral test do not acquire a new testing obligation from these examples.

## Failure modes and result

For each test, identify which production behavior makes its assertion pass or fail and which dependency contract the fixture preserves. If an assertion measures only a mock or unrelated condition, correct that assertion or fixture. Return the covered criterion, actual observation and limits; verification-before-completion owns the resulting completion claim.
