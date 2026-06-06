---
name: using-git-worktrees
description: Use when starting feature work or before executing an implementation plan. Creates an isolated git worktree with smart directory selection and safety checks.
compatibility:
  - "Python 3.11+ standard library"
---

# Using Git Worktrees
## Contents

- [Overview](#overview)
- [Directory Selection Process](#directory-selection-process)
  - [1. Check for an Existing Directory](#1-check-for-an-existing-directory)
  - [2. Check CLAUDE.md](#2-check-claudemd)
  - [3. Ask the User](#3-ask-the-user)
- [Safety Verification](#safety-verification)
  - [Project-Local Directory (.worktrees or worktrees)](#project-local-directory-worktrees-or-worktrees)
  - [Global Directory (~/.config/coding-convention/worktrees)](#global-directory-configcoding-conventionworktrees)
- [Creation Steps](#creation-steps)
  - [1. Detect the Project Name](#1-detect-the-project-name)
  - [2. Create the Worktree](#2-create-the-worktree)
  - [3. Run Project Setup](#3-run-project-setup)
  - [4. Verify a Clean Baseline](#4-verify-a-clean-baseline)
  - [5. Report the Location](#5-report-the-location)
- [Quick Reference](#quick-reference)
- [Common Mistakes](#common-mistakes)
  - [Skipping Ignore Verification](#skipping-ignore-verification)
  - [Assuming the Directory Location](#assuming-the-directory-location)
  - [Proceeding with Failing Tests](#proceeding-with-failing-tests)
  - [Hardcoding Setup Commands](#hardcoding-setup-commands)
- [Example Workflow](#example-workflow)
- [Red Flags](#red-flags)
- [Integration](#integration)


## Overview

A git worktree shares the same repository while creating an isolated workspace, so you can work on multiple branches at the same time without switching your current workspace.

Core principle: systematic directory selection plus safety verification equals reliable isolation.

Announcement at start: "I'm using the using-git-worktrees skill to set up an isolated workspace."

## Directory Selection Process

Follow this priority order:

### 1. Check for an Existing Directory

```bash
# Check in priority order
ls -d .worktrees 2>/dev/null     # preferred (hidden)
ls -d worktrees 2>/dev/null      # alternative
```

If found: use that directory. If both exist, `.worktrees` takes priority.

### 2. Check CLAUDE.md

```bash
grep -i "worktree.*director" CLAUDE.md 2>/dev/null
```

If a preference is specified: use it without asking.

### 3. Ask the User

If there is no directory and no CLAUDE.md preference:

```
No worktree directory found. Where should I create worktrees?

1. .worktrees/ (project-local, hidden)
2. ~/.config/coding-convention/worktrees/<project-name>/ (global location)

Which would you prefer?
```

## Safety Verification

### Project-Local Directory (.worktrees or worktrees)

Before creating a worktree, you must verify that the directory is ignored:

```bash
# Check whether the directory is ignored (respects local, global, and system gitignore)
git check-ignore -q .worktrees 2>/dev/null || git check-ignore -q worktrees 2>/dev/null
```

If it is not ignored:

Follow the project rule: fix broken things immediately.
1. Add the appropriate line to .gitignore
2. Commit the change
3. Proceed with creating the worktree

Why this matters: it prevents accidentally committing worktree contents into the repository.

### Global Directory (~/.config/coding-convention/worktrees)

No .gitignore verification needed. It is outside the project.

## Creation Steps

### 1. Detect the Project Name

```bash
project=$(basename "$(git rev-parse --show-toplevel)")
```

### 2. Create the Worktree

```bash
# Determine the full path
case $LOCATION in
  .worktrees|worktrees)
    path="$LOCATION/$BRANCH_NAME"
    ;;
  ~/.config/coding-convention/worktrees/*)
    path="~/.config/coding-convention/worktrees/$project/$BRANCH_NAME"
    ;;
esac

# Create the worktree with a new branch
git worktree add "$path" -b "$BRANCH_NAME"
cd "$path"
```

### 3. Run Project Setup

Auto-detect and run the appropriate setup:

```bash
# Node.js
if [ -f package.json ]; then npm install; fi

# Rust
if [ -f Cargo.toml ]; then cargo build; fi

# Python
if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
if [ -f pyproject.toml ]; then poetry install; fi

# Go
if [ -f go.mod ]; then go mod download; fi
```

### 4. Verify a Clean Baseline

Run the tests to ensure the worktree starts in a clean state:

```bash
# Example - use the command that fits the project
npm test
cargo test
pytest
go test ./...
```

On test failure: report the failure and ask whether to proceed or investigate.

On test pass: report ready.

### 5. Report the Location

```
Worktree ready at <full-path>
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

## Quick Reference

| Situation | Action |
|--------|--------|
| `.worktrees/` exists | use it (verify ignored) |
| `worktrees/` exists | use it (verify ignored) |
| both exist | use `.worktrees/` |
| neither exists | check CLAUDE.md, then ask the user |
| directory not ignored | add to .gitignore and commit |
| baseline test failure | report failure and ask |
| no package.json/Cargo.toml | skip dependency install |

## Common Mistakes

### Skipping Ignore Verification

- Problem: worktree contents get tracked and pollute git status
- Fix: always use `git check-ignore` before creating a project-local worktree

### Assuming the Directory Location

- Problem: creates inconsistency and violates project conventions
- Fix: follow the priority order: existing > CLAUDE.md > ask the user

### Proceeding with Failing Tests

- Problem: cannot distinguish new bugs from existing issues
- Fix: report the failure, get explicit permission, then proceed

### Hardcoding Setup Commands

- Problem: causes problems in projects that use different tools
- Fix: auto-detect from project files (package.json and so on)

## Example Workflow

```
You: I'm using the using-git-worktrees skill to set up an isolated workspace.

[Check .worktrees/ - exists]
[Verify ignored - git check-ignore confirms .worktrees/ is ignored]
[Create worktree: git worktree add .worktrees/auth -b feature/auth]
[Run npm install]
[Run npm test - 47 passing]

Worktree ready at <repo>/.worktrees/auth
Tests passing (47 tests, 0 failures)
Ready to implement auth feature
```

## Red Flags

Never:
- create a worktree without verifying it is ignored (project-local)
- skip baseline test verification
- proceed with failing tests (without asking)
- assume the directory location when it is ambiguous
- skip checking CLAUDE.md

Always:
- follow the directory priority: existing > CLAUDE.md > ask the user
- verify ignored for project-local
- auto-detect and run project setup
- verify a clean test baseline

## Integration

Called by:
- brainstorming (step 4) - required when the design is approved and implementation proceeds
- subagent-driven-development - required before running any task
- executing-plans - required before running any task
- any skill that needs an isolated workspace

Pairs with:
- finishing-a-development-branch - required for cleanup after work is complete
```
