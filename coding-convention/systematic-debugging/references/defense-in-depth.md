# Defense-in-Depth Verification
## Contents

- [Overview](#overview)
- [Why Multiple Layers](#why-multiple-layers)
- [The Four Layers](#the-four-layers)
  - [Layer 1: Entry-point verification](#layer-1-entry-point-verification)
  - [Layer 2: Business-logic verification](#layer-2-business-logic-verification)
  - [Layer 3: Environment guard](#layer-3-environment-guard)
  - [Layer 4: Debug instrumentation](#layer-4-debug-instrumentation)
- [Applying the Pattern](#applying-the-pattern)
- [Session Example](#session-example)
- [Key Insight](#key-insight)


## Overview

When fixing a bug caused by bad data, adding verification in one place looks sufficient. However, that single check can be bypassed by other code paths, refactoring, or mocks.

○ Core principle: verify at every layer the data passes through. Make the bug structurally impossible.

## Why Multiple Layers

- Single verification: "fixed the bug"
- Multiple layers: "made the bug impossible"

Different layers catch different cases.

- Entry verification catches most bugs
- Business logic catches edge cases
- Environment guards block context-specific risks
- Debug logging helps when the other layers fail

## The Four Layers

### Layer 1: Entry-point verification

Purpose: reject obviously invalid input at the API boundary

```typescript
function createProject(name: string, workingDirectory: string) {
  if (!workingDirectory || workingDirectory.trim() === '') {
    throw new Error('workingDirectory cannot be empty');
  }
  if (!existsSync(workingDirectory)) {
    throw new Error(`workingDirectory does not exist: ${workingDirectory}`);
  }
  if (!statSync(workingDirectory).isDirectory()) {
    throw new Error(`workingDirectory is not a directory: ${workingDirectory}`);
  }
  // ... proceed
}
```

### Layer 2: Business-logic verification

Purpose: ensure the data is meaningful for this operation

```typescript
function initializeWorkspace(projectDir: string, sessionId: string) {
  if (!projectDir) {
    throw new Error('projectDir required for workspace initialization');
  }
  // ... proceed
}
```

### Layer 3: Environment guard

Purpose: prevent dangerous operations in specific contexts

```typescript
async function gitInit(directory: string) {
  // In tests, reject git init outside the temp directory
  if (process.env.NODE_ENV === 'test') {
    const normalized = normalize(resolve(directory));
    const tmpDir = normalize(resolve(tmpdir()));

    if (!normalized.startsWith(tmpDir)) {
      throw new Error(
        `Refusing git init outside temp dir during tests: ${directory}`
      );
    }
  }
  // ... proceed
}
```

### Layer 4: Debug instrumentation

Purpose: capture context for forensics

```typescript
async function gitInit(directory: string) {
  const stack = new Error().stack;
  logger.debug('About to git init', {
    directory,
    cwd: process.cwd(),
    stack,
  });
  // ... proceed
}
```

## Applying the Pattern

When you find a bug.

1. Trace the data flow. Where does the bad value start, where is it used
2. Map every checkpoint. List every point the data passes through
3. Add verification at each layer. Entry, business, environment, debug
4. Test each layer. Try to bypass Layer 1 and confirm Layer 2 catches it

## Session Example

Bug: an empty `projectDir` triggers `git init` in the source code

Data flow.

1. Test setup -> empty string
2. `Project.create(name, '')`
3. `WorkspaceManager.createWorkspace('')`
4. `git init` runs in `process.cwd()`

The four layers added.

- Layer 1: `Project.create()` verifies non-empty, exists, and writable
- Layer 2: `WorkspaceManager` verifies projectDir is non-empty
- Layer 3: `WorktreeManager` rejects git init outside tmpdir during tests
- Layer 4: stack-trace logging before git init

Result: all 1847 tests pass, the bug cannot be reproduced

## Key Insight

All four layers were needed. During testing, each layer caught a bug the others missed.

- Another code path bypassed entry verification
- A mock bypassed the business-logic check
- An edge case on a different platform required the environment guard
- Debug logging identified structural misuse

Do not stop at one verification point. Add checks at every layer.
