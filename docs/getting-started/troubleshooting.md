# Troubleshooting

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/getting-started/troubleshooting.md)

This document is a fast recovery playbook for cases where a user cannot pull the latest repository docs while installing or updating Ghost-ALICE. The GitHub Wiki page `install-troubleshooting` links to this guide online, so users can read it without updating their checkout.

- Wiki: https://github.com/AidALL/ghost-alice/wiki/install-troubleshooting
- Repo copy: `docs/getting-started/troubleshooting.md`

## Contents

- [Git Update Recovery](#git-update-recovery)
- [Target Symptoms](#target-symptoms)
- [1. Fix Identity Errors First](#1-fix-identity-errors-first)
- [2. Inspect Current State](#2-inspect-current-state)
- [3. When Local Changes Are Not Needed](#3-when-local-changes-are-not-needed)
- [4. When Local Changes Must Be Preserved](#4-when-local-changes-must-be-preserved)
- [5. Run the Installer After Git Is Clean](#5-run-the-installer-after-git-is-clean)
- [6. Operating Principles](#6-operating-principles)
- [Privileged Adapter Registration Mismatch](#privileged-adapter-registration-mismatch)
- [Native Hook Trust And Reload](#native-hook-trust-and-reload)

## Git Update Recovery

### Target Symptoms

If any of the following appears, the installer is not the first problem. The local checkout is blocked from synchronizing and must be repaired first.

- `Committer identity unknown`
- `CONFLICT (content)`
- `CONFLICT (add/add)`
- `Automatic merge failed; fix conflicts and then commit the result.`
- `error: Your local changes to the following files would be overwritten by merge`
- `error: your local changes to the following files would be overwritten by merge` (the same error shown by a Korean-locale Git installation)
- `## main...origin/main [ahead N, behind M]`
- `git status --short` shows `UU` or `AA`

Do not keep rerunning the installer in this state. Clean up the Git state first, then rerun the installer.

For normal source updates, prefer the safe installer path instead of raw `git pull`:

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

This command saves source-local tracked and untracked changes in `git stash`, fast-forwards the checkout, and leaves the stash for explicit review.

On Windows, use `.\install.cmd` for the same installer path. If PowerShell shows `cannot be loaded because running scripts is disabled`, the wrapper invokes PowerShell with `-NoProfile -ExecutionPolicy Bypass` and does not change the user or machine execution policy.

If raw `git pull` is already blocked before the checkout can receive `--update-source`, use the bootstrap one-command update:

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

The bootstrap updater runs from the fetched remote blob independently of the local installer, saves source-local tracked and untracked changes in `git stash`, fast-forwards `~/ghost-alice`, and then runs the updated installer. For non-default checkout locations, set `GHOST_ALICE_SOURCE_DIR=/path/to/ghost-alice` before the command.

### 1. Fix Identity Errors First

`Committer identity unknown` means Git cannot create a merge commit because author identity is missing. Configure it once with the user's own account values.

```bash
git config --global user.email "you@example.com"
git config --global user.name "your-name"
```

If a conflict has already started, do not repeat `git pull` immediately after setting identity. Move to conflict recovery in step 2, then choose step 3 or step 4 according to whether local changes must be preserved.

### 2. Inspect Current State

```bash
git status --short --branch
git diff --name-only --diff-filter=U
```

If `UU` or `AA` appears, the checkout is already in a merge conflict. Decide first whether local changes must be preserved or can be discarded.

### 3. When Local Changes Are Not Needed

Use this path only for deployment clones, installer-only clones, or checkouts with no personal edits where local changes can be discarded.

If `git status --short` already shows `UU` or `AA`, abort the merge first.

```bash
git merge --abort
```

Then refetch upstream and align the local checkout with the public `main`.

```bash
git fetch origin
git reset --hard origin/main
git clean -nd
git clean -fd
git pull --ff-only
```

Cautions:

- `git reset --hard origin/main` moves tracked-file content and the local commit position to `origin/main`.
- `git clean -nd` is a delete preview.
- `git clean -fd` actually deletes untracked files and directories. Do not run it if the preview lists anything that must be kept.

### 4. When Local Changes Must Be Preserved

If local docs, skills, or scripts were edited, do not run a destructive reset first. Use the safe source update path or create a backup branch and save diffs.

Preferred path for a normal fast-forward update:

```bash
cd ~/ghost-alice
bash install.sh --update-source
git stash list
git stash show -p stash@{0}
```

If the local installer lacks the update option, use the bootstrap path in [Git Update Recovery](#git-update-recovery).

Only reapply the stash if those source-local changes are still intentional:

```bash
git stash pop stash@{0}
```

Manual backup path:

```bash
git status --short --branch
git branch backup/before-update-YYYYMMDD-HHMM
git diff > ghost-alice-local.diff
git diff --staged > ghost-alice-local-staged.diff
```

Then share `git status --short --branch`, the conflict file list, and both diff files with the maintainer. To resolve it directly, remove conflict markers, run tests, and commit the result.

### 5. Run the Installer After Git Is Clean

After source recovery, reinstall Core and Ghost-ALICE Autopilot together. `--update-source` alone refreshes Core source; the addon is fetched by the paired installation command. On Windows, use `.\install.cmd --addon autopilot`, then `--doctor` and `--status`:

```bash
bash install.sh --addon autopilot
bash install.sh --doctor
bash install.sh --status
```

If `Doctor` or `Status` reports a pending merge warning, the installer created a protective backup during install. Open a fresh Claude/Codex session and ask it to review backed-up changes so the merge-companion flow can decide what to keep.

### 6. Operating Principles

- Update commands should use the safe installer updater by default; the updater itself fast-forwards after saving source-local changes.
- If `git pull` creates a merge conflict, the installer cannot resolve that conflict for the user.
- A blocked-user guide is not enough when it exists only inside the repo. Keep an online source link available from the Wiki so the guide can be read without pulling.
- Do not run the installer while `UU` or `AA` remains in Git status.

## Privileged Adapter Registration Mismatch

The Core hook runner pins each privileged adapter's script hash, event and source in its registered command. A mismatch rejects that invocation with exit 126 before the adapter executes. The rejection message identifies `hash`, `event` or `source`; the strict hook log retains the diagnostic under `surface_item.rejection_diagnostic` and the original native session.

For a hash mismatch, compare the rejected command's expected hash, the installed script's actual hash and the currently registered command. If the current registration and installed script match but the rejected invocation expects an older hash, that invocation used an older registration. Refresh the host's loaded hook commands through its available session/restart controls. A registration-file update alone is not evidence that an existing host consumed it. If the current registration also differs from the installed script, repair the managed installation and registration together after preserving local changes. Event/source mismatches require repair of the registered mapping through the Core installer.

Do not bypass hash validation, regenerate historical proof timestamps, or repeat unchanged business verification to fix registration. Confirm recovery from a subsequent native Ghost-ALICE Autopilot callback. Distinguish an accepted `pending-adapter` publication from an `applied` result and its application reference in the owning session's Stop log.

## Native Hook Trust And Reload

Check installation, native trust and running-host adoption separately. The installed script and registered command can match while the host retains a previous command or trust decision. A native `modified` hook state identifies a trust mismatch; it is different from the Core adapter runner's exit 126 rejection.

1. Preserve local configuration, then use the Core installer to synchronize the managed hook command and its native trust entry. Review the exact changed command before accepting its new hash. Keep unrelated trust entries and each hook's enabled/disabled choice unchanged.
2. Use the host's available reload controls. If the active host still uses an older command or trust entry, fully exit and reopen it; starting another conversation alone is not proof of reload.
3. Observe the next actual input, routing and Ghost-ALICE Autopilot callback in that session. Installed files, a separate diagnostic process and a successful publication request do not establish what the active host invoked.

Keep strict audit enabled. Restore the affected registration or trust binding without disabling the guard or replaying completed business checks. A remaining recording failure is reported separately from the supported business result.
