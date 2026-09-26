---
name: finishing-a-development-branch
description: Use when implementation is ready for integration, when the user asks to merge or publish a pull request, or when the branch finishing path remains undecided.
calls:
  - "soft:using-git-worktrees"
  - "soft:requesting-code-review"
compatibility:
  - "Python 3.11+ standard library"
---

# Finishing a Development Branch
## Contents

- [Overview](#overview)
- [Process](#process)
  - [Step 1: CI preflight](#step-1-ci-preflight)
  - [Step 2: Determine the base branch](#step-2-determine-the-base-branch)
  - [Step 3: Resolve the integration path](#step-3-resolve-the-integration-path)
  - [Step 4: Execute the selection](#step-4-execute-the-selection)
    - [Before integrating a pull request](#before-integrating-a-pull-request)
  - [Step 5: Worktree cleanup](#step-5-worktree-cleanup)
- [Quick reference](#quick-reference)
- [Common mistakes](#common-mistakes)
- [Red flags](#red-flags)
- [Integration](#integration)


## Overview

Guide the completion of development work with clear options and handle the selected workflow.

Core principle: reproduce relevant CI locally -> honor the selected path -> reconcile remote CI and review -> integrate the verified head -> clean up only when safe.

Announcement at start: "I'm using the finishing-a-development-branch skill to complete this work."

## Process

### Step 1: CI preflight

Before the first push or an amend intended for publication, inspect the actual CI workflow definitions and the scripts or reusable workflows they invoke. Determine the applicable jobs from their triggers, path filters, matrix, and the proposed change. Record the commands, runner/runtime versions, dependencies, environment requirements, and generated-file or documentation checks. Do not substitute a remembered test command or a representative subset of tests for this inventory. If the repository has no CI workflow, use its documented validation contract and say that no remote workflow exists.

Run all relevant locally executable equivalents, including setup and generated-artifact checks, with the workflow's runtime versions and flags where available. Identical commands under identical conditions may share one result; distinct matrix conditions may not. Match missing runtimes when feasible. Record unavoidable OS, runtime, service, credential, or runner differences explicitly, with the checks that remain remote-only. A pass on a different runtime is evidence for that local environment, not proof of runner parity. An unavailable runner does not excuse skipping the remaining checks that can run locally.

Bind results to the tested tree and environment. After an edit, regeneration, amend, rebase, or dependency/configuration change, invalidate and rerun every affected check before pushing. Reuse unaffected results only with an explicit unchanged-input basis; if impact cannot be bounded, rerun the applicable workflow set. Documentation-only changes can still affect literal assertions, link checks, catalog parity, and packaging checks. An unchanged tree after a metadata-only amend can retain local results, but the new head still needs its own remote CI and review evidence.

Fix observed failures before pushing. Do not add skip flags, weaken assertions, disable jobs, or mark required checks optional merely to obtain a green result. A legitimate contract change must retain meaningful coverage. Report unavoidable remote-only gaps honestly and proceed with an authorized branch push only after runnable checks pass; this is not permission to integrate with unverified required CI.

Record a compact preflight matrix: workflow/job, applicable command, required versus actual environment, tested tree, exit status, skipped or remote-only coverage, and reason. The preflight reduces preventable remote failures; it does not guarantee that CI cannot fail.

When tests fail:
```
Tests failing (<N> failures). Must fix before completing:

[Show failures]

Cannot push or integrate until runnable checks pass.
```

Stop. Do not proceed to Step 2.

When runnable checks pass and runner gaps are recorded: proceed to Step 2. After pushing, require successful applicable remote CI for the exact head before integration. A green older SHA, local pass, clean code review, pending job, or silently skipped required job does not satisfy that condition.

### Step 2: Determine the base branch

```bash
# Try the common base branch
git merge-base HEAD main 2>/dev/null || git merge-base HEAD master 2>/dev/null
```

Or ask: "This branch split from main - is that correct?"

### Step 3: Resolve the integration path

First use the user's existing instruction. If they already authorized merging, pushing a PR, or retaining the branch, carry out that path after its checks; do not ask them to choose again. Authorization to merge does not imply permission to skip review.

Only when the integration path remains undecided, present the following 4 options:

```
Implementation complete. What would you like to do?

1. Merge back to <base-branch> locally
2. Push and create a Pull Request
3. Keep the branch as-is (I'll handle it later)
4. Discard this work

Which option?
```

Keep the options concise and explain only a concrete blocker or consequence the user needs to decide.

### Step 4: Execute the selection

#### Before integrating a pull request

Load `requesting-code-review` and complete its Remote Pull Request Review Gate before merging, fast-forwarding the base branch, or claiming the PR is ready to integrate. Read the current head's submitted reviews, inline findings, conversation summaries, and unresolved threads, including relevant findings carried from earlier PRs. Passing CI and local review do not replace this readback. Pending, unavailable, or stale review remains unverified until reconciled; do not merge merely because the checks are green.

Also confirm the applicable remote CI jobs succeeded for that exact head. CI and review are separate conditions; neither substitutes for the other. After changes prompted by either one, repeat affected local preflight before pushing and obtain both remote results for the replacement head.

Fix valid findings within the user's scope, and reject incorrect or conflicting suggestions with code/test evidence. Record the reviewed head, source completion, finding dispositions, and remaining limitations. After any amend, rebase, or squash, reread and obtain required review for the new head. Recheck the remote head immediately before integration; if it differs from the reconciled head, repeat the gate. If no PR exists for a purely local integration, do not invent a remote review dependency.

When the user requests one commit, consolidate the unmerged work before the final review. An authorized replacement of a published PR branch uses `--force-with-lease` with the expected prior head and targets only that PR branch. A one-commit request alone does not authorize rewriting published `main` or another shared base branch. Work already merged needs a follow-up commit unless the user specifically authorizes replacing an identified published range. With that separate authorization, preserve recovery refs, verify that the replacement contains exactly the authorized work without dropping unrelated changes, and use an explicit expected-head lease on the authorized target ref. If the remote head moves or the range cannot be reconciled, stop the push and inspect; do not broaden the authorization or overwrite the new work. Do not re-ask for permission already granted for that exact operation. A rewritten head invalidates the prior review's freshness even when tests remain green.

#### Option 1: Local merge

Apply the remote review gate first when this branch has an associated PR. Preserve the user's requested merge strategy and authorship; the commands below illustrate a local merge when no different strategy was specified.

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

Complete Step 1 on the final proposed tree before this first push and before publishing an amended replacement.

```bash
# Push the branch
git push -u origin <feature-branch>

# Write the exact PR description to a file, then create the PR
gh pr create --title "<title>" --body-file <prepared-body-file>
```

Keep the branch and worktree while review or follow-up changes are pending. If the user also authorized merging, continue through the remote review gate and integrate the reconciled head; do not stop at PR creation. Posting review requests or bot-trigger comments requires existing user authorization for that communication or an explicitly invoked workflow that authorizes it.

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

For a completed merge or confirmed discard (options 1 and 4), first ensure no uncommitted work or ongoing review/fix task needs the checkout. Never delete the working directory of an active task.

Check whether you are in a worktree:
```bash
git worktree list
```

If so:
```bash
git worktree remove <worktree-path>
```

For an open PR or keep-as-is choice (options 2 and 3): keep the worktree. A PR may need review fixes even when CI has passed.

## Quick reference

| Option | Merge | Push | Keep worktree | Clean up branch |
|--------|------|------|------------|---------|
| 1. Local merge | ✓ | - | - | ✓ |
| 2. Create PR | - | ✓ | ✓ | - |
| 3. Keep as-is | - | - | ✓ | - |
| 4. Discard | - | - | - | ✓ (forced) |

## Common mistakes

Partial CI preflight
- Problem: pushing after a focused subset passes, then discovering a locally reproducible workflow failure remotely
- Fix: inspect the actual workflow jobs and execute all relevant local equivalents; disclose unavoidable runner differences

Open questions
- Problem: "What should I do next?" -> ambiguous
- Fix: honor the existing integration instruction; offer 4 structured options only when the path is undecided

Unread remote review
- Problem: treating green checks or a local review as proof that automated PR findings were handled
- Fix: read every remote review surface, reconcile relevant earlier findings, and bind completion to the head being integrated

Automatic worktree cleanup
- Problem: removing a worktree that may still be needed (options 2, 3)
- Fix: clean up only for options 1 and 4

No discard confirmation
- Problem: deleting work by mistake
- Fix: require a typed "discard" confirmation

## Red flags

Never:
- proceed with failing tests
- push with known runnable preflight failures, stale affected results, or checks omitted in favor of a representative subset
- bypass CI with skip flags or weakened checks to manufacture a pass
- merge without verifying tests on the result
- integrate before applicable remote CI succeeds for the exact head
- merge with unread remote findings or a pending, unavailable, or stale required review
- delete work without confirmation
- force push without authorization, or rewrite published base history outside a specifically authorized range

Always:
- complete workflow-derived local preflight before pushing or publishing an amend
- follow the user's selected path without repeating the option question
- complete the remote review gate on the current PR head before integration
- get a typed confirmation for option 4
- clean up the worktree only for options 1 and 4

## Integration

Called by:
- subagent-driven-development (Step 7) - after all tasks are complete
- executing-plans (Step 5) - after all batches are complete

Pairs with:
- using-git-worktrees - clean up worktrees created by that skill
