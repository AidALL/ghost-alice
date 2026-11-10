---
name: finishing-a-development-branch
description: "For a branch ready to integrate, runs its locally runnable CI jobs before any push or merge and carries out the human user's choice, asking the human user if undecided: merge locally, open or merge a pull request, keep, or delete the branch."
calls:
  - "soft:using-git-worktrees"
  - "soft:requesting-code-review"
compatibility:
  - "Python 3.11+ standard library"
---

# Finishing a Development Branch

## Overview

Offer clear options for completing development work and carry out the workflow that the human user selected.

Principle: reproduce relevant CI locally -> honor the path that the human user selected -> reconcile remote CI and review -> integrate the verified head -> clean up only when safe.

## Process

### Step 1: CI preflight

Before the first push or an amend intended for publication, inspect the actual CI workflow definitions and the scripts or reusable workflows they invoke. Determine the applicable jobs from their triggers, path filters, matrix, and the proposed change. Record the commands, runner/runtime versions, dependencies, environment requirements, and generated-file or documentation checks. Do not substitute a remembered test command or a representative subset of tests for this inventory. If the repository has no CI workflow, use its documented validation contract and say that no remote workflow exists.

Run all relevant locally executable equivalents, including setup and generated-artifact checks, with the workflow's runtime versions and flags where available. Identical commands under identical conditions may share one result; distinct matrix conditions may not. Match missing runtimes when feasible. Record unavoidable OS, runtime, service, credential, or runner differences explicitly, with the checks that remain remote-only. A pass on a different runtime is evidence for that local environment, not proof of runner parity. An unavailable runner does not excuse skipping the remaining checks that can run locally.

Bind results to the tested tree and environment. After an edit, regeneration, amend, rebase, or dependency/configuration change, invalidate and rerun every affected check before pushing. Reuse unaffected results only with an explicit unchanged-input basis; if impact cannot be bounded, rerun the applicable workflow set. Documentation-only changes can still affect literal assertions, link checks, catalog parity, and packaging checks. An unchanged tree after a metadata-only amend can retain local results, but the new head still needs its own remote CI and review evidence.

Fix observed failures before pushing. Do not add skip flags, weaken assertions, disable jobs, or mark required checks optional merely to obtain a green result. A legitimate contract change must retain meaningful coverage. Report unavoidable remote-only gaps honestly and proceed with a branch push that the human user authorized only after runnable checks pass; this is not permission to integrate with unverified required CI.

Record a compact preflight matrix: workflow/job, applicable command, required versus actual environment, tested tree, exit status, skipped or remote-only coverage, and reason. The preflight reduces preventable remote failures; it does not guarantee that CI cannot fail.

When tests fail:
```
Tests failing (<N> failures). Must fix before completing:

[Show failures]

Cannot push or integrate until runnable checks pass.
```

Suspend the push or integration that depends on those checks. Correct the observed failure within what the human user's instructions already authorize.

When runnable checks pass and runner gaps are recorded: proceed to Step 2. After pushing, require successful applicable remote CI for the exact head before integration. A green older SHA, local pass, clean code review, pending job, or silently skipped required job does not satisfy that condition.

### Step 2: Determine the base branch

Use the target that the human user specified, the target of a PR whose base the human user set, or the inspected repository default; a PR base that the response model set does not identify the integration target. A merge-base with main or master shows ancestry but does not by itself identify the intended integration target. Ask the human user only when these leave the integration target unresolved.

### Step 3: Resolve the integration path

First use the human user's existing instruction. If the human user already authorized merging, pushing a PR, or retaining the branch, carry out that path after its checks. The human user's authorization to merge does not imply permission to skip review.

Only when the integration path remains undecided, ask the human user, in the human user's language, to choose one of Step 4's four options: local merge, push and create a pull request, keep as-is, or discard.

Keep the options concise and explain only a concrete blocker or consequence the user needs to decide.

### Step 4: Execute the human user's selection

#### Before integrating a pull request

The response model follows requesting-code-review's [Remote Pull Request Review Gate](../requesting-code-review/SKILL.md#remote-pull-request-review-gate). That procedure returns the reconciled head, required review/CI results and finding dispositions. Integrate only the head those results support. This gate adds no remote review to a purely local integration without a PR.

When the human user requests one commit, consolidate the unmerged work before the final review. A replacement of a published PR branch that the human user authorized uses `--force-with-lease` with the expected prior head and targets only that PR branch. A one-commit request alone does not authorize rewriting published `main` or another shared base branch. Work already merged needs a follow-up commit unless the human user specifically authorizes replacing an identified published range. With that separate authorization from the human user, preserve recovery refs, verify that the replacement contains exactly the work that the human user's instructions authorize without dropping unrelated changes, and use an explicit expected-head lease on the target ref that the human user authorized. If the remote head moves or the range cannot be reconciled, stop the push and inspect; do not broaden the authorization or overwrite the new work. A rewritten head invalidates the prior review's freshness even when tests remain green.

#### Option 1: Local merge

Apply the remote review gate first when this branch has an associated PR. Preserve the merge strategy and authorship that the human user requested; the commands below illustrate a local merge when the human user specified no different strategy.

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

Keep the branch and worktree while review or follow-up changes are pending. If the human user also authorized merging, continue through the remote review gate and integrate the reconciled head; do not stop at PR creation. Posting review requests or bot-trigger comments requires the human user's existing authorization for that communication, including through a workflow that the human user explicitly invoked for that post.

#### Option 3: Keep as-is

Report: "Keeping branch <name>. Worktree preserved at <path>."

Do not clean up the worktree.

#### Option 4: Discard

Ask the human user to confirm first:
```
This will permanently delete:
- Branch <name>
- All commits: <commit-list>
- Worktree at <path>

Type 'discard' to confirm.
```

Wait for the human user's exact confirmation.

On the human user's confirmation:
```bash
git checkout <base-branch>
git branch -D <feature-branch>
```

After that: clean up the worktree (Step 5)

### Step 5: Worktree cleanup

For a completed merge or a discard the human user confirmed (options 1 and 4), first ensure no uncommitted work or ongoing review/fix task needs the checkout. Never delete the working directory of an active task.

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

A selected integration path does not authorize unrelated changes or messages.

Verification-before-completion owns completion claims and evidence reuse; systematic-debugging owns failed attempts. The output states the actual integration result, verified head, remaining gaps and preserved or removed checkout.
