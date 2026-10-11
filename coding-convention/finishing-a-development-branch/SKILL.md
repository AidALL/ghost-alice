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

Suspend the push or integration that depends on those checks. Correct the observed failure within existing authority; independent authorized work may continue.

When runnable checks pass and runner gaps are recorded: proceed to Step 2. After pushing, require successful applicable remote CI for the exact head before integration. A green older SHA, local pass, clean code review, pending job, or silently skipped required job does not satisfy that condition.

### Step 2: Determine the base branch

Use the PR target, user's specified target or inspected repository default. A merge-base with main or master shows ancestry but does not by itself identify the intended integration target. Ask only when the authorized target remains unresolved.

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

The current-session assistant follows requesting-code-review's [Remote Pull Request Review Gate](../requesting-code-review/SKILL.md#remote-pull-request-review-gate). That procedure returns the reconciled head, required review/CI results and finding dispositions. Integrate only the head those results support. A purely local integration without a PR has no invented remote-review dependency.

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

## Failure modes

The process above defines each prerequisite once. Do not substitute a representative test subset for required preflight, local review for remote review, or an older successful head for the head being integrated. A new remote head requires its affected checks and review.

A published-history rewrite needs the exact authorization and expected-head lease defined in Step 4. A selected integration path does not authorize unrelated changes or messages. Keep the checkout while an open PR, uncommitted work or active task needs it.

Verification-before-completion owns completion claims and evidence reuse; systematic-debugging owns failed attempts. The output states the actual integration result, verified head, remaining gaps and preserved or removed checkout.

## Integration

Called by:
- subagent-driven-development - after all tasks and final review are complete
- executing-plans (Step 3: Finish development) - after all tasks are complete and verified

Pairs with:
- using-git-worktrees - clean up worktrees created by that skill
