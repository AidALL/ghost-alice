# Troubleshooting

[🇺🇸 English](./troubleshooting.md) | [🇰🇷 한국어](../ko/getting-started/troubleshooting.md)

This document is a fast recovery playbook for cases where a user cannot pull the latest repository docs while installing or updating Ghost-ALICE. The GitHub Wiki home links to this guide online, so users can read it without updating their checkout.

- Wiki: [GitHub Wiki home](https://github.com/AidALL/ghost-alice/wiki)
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
- [Native Hook Trust And Reload](#native-hook-trust-and-reload)
- [Intake Warning And Doctor Lists](#intake-warning-and-doctor-lists)

## Git Update Recovery

### Target Symptoms

If any of the following appears, the installer is not the first problem. The local checkout is blocked from synchronizing and must be repaired first.

- `Committer identity unknown`
- `CONFLICT (content)`
- `CONFLICT (add/add)`
- `Automatic merge failed; fix conflicts and then commit the result.`
- `error: Your local changes to the following files would be overwritten by merge`
- `error: 다음 파일의 로컬 변경 사항을 병합 때문에 덮어 쓰게 됩니다` (the same error shown by a Korean-locale Git installation)
- `## main...origin/main [ahead N, behind M]`
- `git status --short` shows `UU` or `AA`

Do not keep rerunning the installer in this state. Clean up the Git state first, then rerun the installer.

For normal source updates, prefer the safe installer path instead of raw `git pull`:

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

This command saves local tracked and untracked changes in the checkout in `git stash`, fast-forwards the checkout, and leaves the stash for the human user to review. Git does not record who made those changes.

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

If a conflict has already started, do not repeat `git pull` immediately after setting identity. Inspect the current state in step 2, then use step 3 or step 4 according to the human user's decision to discard or preserve the local changes in the checkout.

### 2. Inspect Current State

```bash
git status --short --branch
git diff --name-only --diff-filter=U
```

If `UU` or `AA` appears, the checkout is already in a merge conflict. The human user first decides whether to preserve or discard the local changes in the checkout.

### 3. When Local Changes Are Not Needed

Use this path only when the human user has decided that the local changes in the checkout can be discarded, such as for a deployment clone or an installer-only clone.

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
- `git clean -fd` actually deletes untracked files and directories. Do not run it if the preview lists anything the human user wants to keep.

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

Only reapply the stash if the human user still wants the local changes that the installer's `--update-source` saved in it:

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

After source recovery, reinstall Core. `--update-source` alone refreshes the Core source; the installation command below installs it, and installed addons stay installed. On Windows, use `.\install.cmd`, then `--doctor` and `--status`:

```bash
bash install.sh
bash install.sh --doctor
bash install.sh --status
```

If `Doctor` or `Status` reports a pending merge warning, the installer created a protective backup during install. Open a fresh Claude Code/Codex session and ask it to review backed-up changes. The response model follows merge-companion to explain the choices, and the human user decides what to keep.

### 6. Operating Principles

- Update commands should use the safe installer updater by default; the updater itself fast-forwards after saving source-local changes.
- If `git pull` creates a merge conflict, the installer cannot resolve that conflict for the user.
- A blocked-user guide is not enough when it exists only inside the repo. Keep an online source link available from the Wiki so the guide can be read without pulling.
- Do not run the installer while `UU` or `AA` remains in Git status.

## Native Hook Trust And Reload

Check installation, Codex's hook trust entries and running-host adoption separately. The installer writes Codex's hook trust entries for managed hook commands on every install run; Codex can also record an entry after an answer that trusts the hook's new hash is entered at Codex's check of its hook trust entries. The installed script and registered command can match while the running Codex retains a previous command or hook trust entry. A native `modified` hook state identifies a trust mismatch.

1. Preserve local configuration, then run the Core installer: on every install run it writes the managed hook command and writes the command's new trusted hash (`trusted_hash`) into Codex's hook trust entry. Keep unrelated hook trust entries and each hook's enabled/disabled choice unchanged.
2. Use the host's available reload controls. If the active host still uses an older command or trust entry, fully exit and reopen it; starting another conversation alone is not proof of reload.
3. Observe the next actual input and routing in that session. Installed files and a separate diagnostic process do not establish what the active host invoked.

Keep Ghost-ALICE hooks and audit records enabled, and run the Core installer of step 1 again, which on every install run restores the affected hook registration and Codex's hook trust entry. Reuse completed business checks under the [evidence reuse rule](../../architecture.md#evidence-and-native-delivery) instead of replaying them.

## Intake Warning And Doctor Lists

Claude Code can show ``Ghost-ALICE intake: records that need an input that Core code matched to the host's transcript row (the response model's `interpreted-as` record, the response route and the completion records) are paused in this session because <reason> (<cause>). They resume at your next input in this session. Later waits with this cause are only recorded; `bash install.sh --doctor` lists them.``. Core code could not match the session's latest prompt to a transcript row carrying the host's human-origin markers, for example for a message another session sent or a prompt whose transcript row it could not find. The session waits for the next input whose transcript row carries the host's human-origin markers. The work that your instructions authorize continues, but Core code stores no `interpreted-as` record that the response model writes, response route or completion until then. The warning appears once per session and cause; later waits with the same cause are only recorded. An [agent visibility profile](./installation.md#agent-visibility-profile) can turn a hook's empty result into a plain-text line; that hook cannot carry the warning, so the warning appears with the next PreToolUse or Stop.

1. Send your next input in that session as usual. When Core code matches the prompt's digest and character count to a transcript row carrying the host's human-origin markers, it records that input as the current input and resumes the dependent recording.
2. Run `bash install.sh --doctor` (Windows: `.\install.cmd --doctor`). `native-intake-waits` lists the sessions that still wait, with the cause. `native-intake-failures` lists the sessions where Core's intake step failed, with the error type, the count and the last time; the hook continued past each failure.
3. If a session still waits after your own typed input, or `native-intake-failures` keeps growing, share those doctor lines with the maintainer. Do not edit the ledger by hand.
