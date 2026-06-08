# Troubleshooting

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/getting-started/troubleshooting.md)

This document is a fast recovery playbook for cases where a user cannot pull the latest repository docs while installing or updating Ghost-ALICE. The same content is also published to the GitHub Wiki page `install-troubleshooting` so users blocked from updating the repo can still read it.

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

Do not keep rerunning `install.ps1`, `install.sh`, or `install.cmd` in this state. Clean up the Git state first, then rerun the installer.

For normal source updates, prefer the safe installer path instead of raw `git pull`:

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

PowerShell:

```powershell
.\install.cmd -UpdateSource
```

These commands save source-local tracked and untracked changes in `git stash`, fast-forward the checkout, and leave the stash for explicit review.

If raw `git pull` is already blocked before the checkout can receive `--update-source`, use the bootstrap one-command update:

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

The bootstrap updater runs from the fetched remote blob instead of the old local installer, saves source-local tracked and untracked changes in `git stash`, fast-forwards `~/ghost-alice`, and then runs the updated installer. For non-default checkout locations, set `GHOST_ALICE_SOURCE_DIR=/path/to/ghost-alice` before the command.

### 1. Fix Identity Errors First

`Committer identity unknown` means Git cannot create a merge commit because author identity is missing. Configure it once with the user's own account values.

```powershell
git config --global user.email "you@example.com"
git config --global user.name "your-name"
```

If a conflict has already started, do not repeat `git pull` immediately after setting identity. Move to conflict recovery in step 3.

### 2. Inspect Current State

```powershell
git status --short --branch
git diff --name-only --diff-filter=U
```

If `UU` or `AA` appears, the checkout is already in a merge conflict. Decide first whether local changes must be preserved or can be discarded.

### 3. When Local Changes Are Not Needed

Use this path only for deployment clones, installer-only clones, or checkouts with no personal edits where local changes can be discarded.

If `git status --short` already shows `UU` or `AA`, abort the merge first.

```powershell
git merge --abort
```

Then refetch upstream and align the local checkout with the public `main`.

```powershell
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

If that option is not available yet because the checkout cannot pull the new installer, use the one-command bootstrap updater instead:

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

Only reapply the stash if those source-local changes are still intentional:

```bash
git stash pop stash@{0}
```

Manual backup path:

```powershell
git status --short --branch
git branch backup/before-update-YYYYMMDD-HHMM
git diff > ghost-alice-local.diff
git diff --staged > ghost-alice-local-staged.diff
```

Then share `git status --short --branch`, the conflict file list, and both diff files with the maintainer. To resolve it directly, remove conflict markers, run tests, and commit the result.

### 5. Run the Installer After Git Is Clean

Windows PowerShell:

```powershell
.\install.cmd
.\install.cmd -Doctor
.\install.cmd -Status
```

If Windows PowerShell prints `cannot be loaded because running scripts is disabled` for `Microsoft.PowerShell_profile.ps1` or `.\install.ps1`, use `.\install.cmd`. The wrapper starts PowerShell with `-NoProfile -ExecutionPolicy Bypass` for this installer run and does not change the user or machine execution policy.

macOS / Linux / WSL / Git Bash:

```bash
bash install.sh
bash install.sh --doctor
bash install.sh --status
```

If `Doctor` or `Status` reports a pending merge warning, the installer created a protective backup during install. Open a fresh Claude/Codex session and ask it to review backed-up changes so the merge-companion flow can decide what to keep.

### 6. Operating Principles

- Update commands should use the safe installer updater by default; the updater itself fast-forwards after saving source-local changes.
- If `git pull` creates a merge conflict, the installer cannot resolve that conflict for the user.
- A blocked-user guide is not enough when it exists only inside the repo. Keep the same recovery playbook somewhere readable without pulling, such as the Wiki or release notes.
- Do not run the installer while `UU` or `AA` remains in Git status.
