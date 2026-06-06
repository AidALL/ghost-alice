---
name: finishing-a-development-branch
description: Use when implementation is complete, tests have passed, and the integration path must be chosen. Presents and executes structured options for finishing a development branch.
calls:
  - "soft:using-git-worktrees"
compatibility:
  - "Python 3.11+ standard library"
---

# Finishing a Development Branch
## Contents

- [Overview](#overview)
- [Process](#process)
  - [Step 1: Test verification](#step-1-test-verification)
  - [Step 2: Determine the base branch](#step-2-determine-the-base-branch)
  - [Step 3: Present options](#step-3-present-options)
  - [Step 4: Execute the selection](#step-4-execute-the-selection)
  - [Step 5: Worktree cleanup](#step-5-worktree-cleanup)
- [Quick reference](#quick-reference)
- [Common mistakes](#common-mistakes)
- [Red flags](#red-flags)
- [Integration](#integration)


## Overview

Guide the completion of development work with clear options and handle the selected workflow.

Core principle: test verification -> present options -> execute the selection -> cleanup.

Announcement at start: "I'm using the finishing-a-development-branch skill to complete this work."

## Process

### Step 1: Test verification

Verify that tests pass before presenting options:

```bash
# Run the project test suite
npm test / cargo test / pytest / go test ./...
```

When tests fail:
```
Tests failing (<N> failures). Must fix before completing:

[Show failures]

Cannot proceed with merge/PR until tests pass.
```

Stop. Do not proceed to Step 2.

When tests pass: proceed to Step 2.

### Step 2: Determine the base branch

```bash
# Try the common base branch
git merge-base HEAD main 2>/dev/null || git merge-base HEAD master 2>/dev/null
```

Or ask: "This branch split from main - is that correct?"

### Step 3: Present options

Present exactly the following 4 options:

```
Implementation complete. What would you like to do?

1. Merge back to <base-branch> locally
2. Push and create a Pull Request
3. Keep the branch as-is (I'll handle it later)
4. Discard this work

Which option?
```

Do not add explanation. Keep the options concise.

### Step 4: Execute the selection

#### Option 1: Local merge

```bash
# Switch to the base branch
git checkout <base-branch>

# Pull the latest state
git pull

# Merge the feature branch
git merge <feature-branch>

# Verify tests on the merge result
<test command>

# When tests pass
git branch -d <feature-branch>
```

After that: clean up the worktree (Step 5)

#### Option 2: Push and create a PR

```bash
# Push the branch
git push -u origin <feature-branch>

# Create the PR
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
<2-3 bullets of what changed>

## Test Plan
- [ ] <verification steps>
EOF
)"
```

After that: clean up the worktree (Step 5)

#### Option 3: Keep as-is

Report: "Keeping branch <name>. Worktree preserved at <path>."

Do not clean up the worktree.

#### Option 4: Discard

Confirm first:
```
This will permanently delete:
- Branch <name>
- All commits: <commit-list>
- Worktree at <path>

Type 'discard' to confirm.
```

Wait for the exact confirmation.

On confirmation:
```bash
git checkout <base-branch>
git branch -D <feature-branch>
```

After that: clean up the worktree (Step 5)

### Step 5: Worktree cleanup

For options 1, 2, and 4:

Check whether you are in a worktree:
```bash
git worktree list | grep $(git branch --show-current)
```

If so:
```bash
git worktree remove <worktree-path>
```

For option 3: keep the worktree.

## Quick reference

| Option | Merge | Push | Keep worktree | Clean up branch |
|--------|------|------|------------|---------|
| 1. Local merge | ✓ | - | - | ✓ |
| 2. Create PR | - | ✓ | ✓ | - |
| 3. Keep as-is | - | - | ✓ | - |
| 4. Discard | - | - | - | ✓ (forced) |

## Common mistakes

Skipping test verification
- Problem: merging broken code, creating a failing PR
- Fix: always verify tests before presenting options

Open questions
- Problem: "What should I do next?" -> ambiguous
- Fix: present exactly 4 structured options

Automatic worktree cleanup
- Problem: removing a worktree that may still be needed (options 2, 3)
- Fix: clean up only for options 1 and 4

No discard confirmation
- Problem: deleting work by mistake
- Fix: require a typed "discard" confirmation

## Red flags

Never:
- proceed with failing tests
- merge without verifying tests on the result
- delete work without confirmation
- force push without an explicit request

Always:
- verify tests before presenting options
- present exactly 4 options
- get a typed confirmation for option 4
- clean up the worktree only for options 1 and 4

## Integration

Called by:
- subagent-driven-development (Step 7) - after all tasks are complete
- executing-plans (Step 5) - after all batches are complete

Pairs with:
- using-git-worktrees - clean up worktrees created by that skill
