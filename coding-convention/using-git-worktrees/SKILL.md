---
name: using-git-worktrees
description: Use only when the authorized development task requires an isolated checkout.
compatibility:
  - "Python 3.11+ standard library"
---

# Using Git Worktrees

The agent selects an isolated checkout when the authorized development task requires isolation. Reuse a suitable attached or existing checkout before creating another. Prefer the host's managed-worktree API; use the shell procedure only when that API is unavailable or unsuitable.

## Select the checkout

Inspect the repository identity, current branch and existing worktrees. Check whether an existing checkout satisfies the task's branch, file ownership and isolation requirements. Preserve its uncommitted work.

For a new shell-managed worktree, use an explicit project or user location. Otherwise prefer an existing `.worktrees/` directory, then `worktrees/`. If neither exists and no project preference settles the choice, ask whether to use a project-local directory or `~/.config/coding-convention/worktrees/<project-name>/`.

Before creating inside the repository, check the exact selected directory with `git check-ignore`. Do not accept a result for a different directory. If it is not ignored, add an ignore rule only within existing authority and verify it, or use an authorized external directory. An external directory needs no repository ignore rule.

## Create and prepare

Resolve the absolute target path, repository and agreed starting ref before issuing a command. For a new branch, use the host/project naming convention. For continuing work, use the requested existing ref rather than silently starting from a default branch.

The shell operation is `git worktree add <absolute-target> -b <new-branch> <starting-ref>` for an authorized new branch. Existing branches require the corresponding existing-branch form. Replace the arguments from inspected state; do not execute the angle-bracket notation.

Follow the project's declared package manager and setup instructions. Do not run every example package manager or install dependencies merely because a file exists. A task prohibition on network, installation or setup overrides the default preparation.

Apply verification-before-completion to the relevant baseline. Reuse unchanged evidence; run affected checks when a new checkout or observed dependency change leaves an unresolved condition. Distinguish a pre-existing failure from the proposed change and correct it only within the authorized task.

## Result and cleanup

Report the absolute checkout, branch/ref and actual baseline result or remaining gap. Do not claim all tests passed from a selected subset. Finish branch integration and cleanup through finishing-a-development-branch.

For managed worktrees, use the host's attach/archive lifecycle. Never delete an active task's checkout or discard uncommitted work. A successful PR does not by itself authorize cleanup.

## Failure modes

Do not create duplicate isolation merely because the skill was invoked. Do not guess a branch or target path, use an ignore result for the wrong directory, attribute an old failure to new work, or claim host adoption from creating a checkout.
