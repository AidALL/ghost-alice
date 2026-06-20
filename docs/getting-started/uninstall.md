# Uninstall Cleanup Procedure

Language: English | [Korean](../ko/getting-started/uninstall.md)

Ghost-ALICE uninstall is driven by the `install-state` manifest written by the installer. The uninstall path removes or restores only entries that the Ghost-ALICE installer recorded as installed or changed.

## Contents

- [Choose The Removal Scope](#choose-the-removal-scope)
- [Basic Commands](#basic-commands)
- [Source Of Truth Files](#source-of-truth-files)
- [Cleanup Order](#cleanup-order)
- [Recovery Principles](#recovery-principles)
- [system_env_changes](#systemenvchanges)

## Choose The Removal Scope

| Goal | Command path | Notes |
| --- | --- | --- |
| Remove all managed Ghost-ALICE surfaces | `bash install.sh --uninstall` | Full uninstall across recorded targets |
| Remove one platform | `bash install.sh --platform codex --uninstall` | Keeps other platform installs |
| Remove selected skills | `bash install.sh --platform codex --uninstall task-router` | Keeps hooks and shared assets when other skills remain |
| Remove one official addon | Follow that addon's repository documentation | Keeps addon hook and sidecar cleanup aligned |

Addon-specific removal is different from selected skill removal. A full uninstall removes the managed Ghost-ALICE footprint, including installed addon sidecar cleanup. If you want to remove only one official addon, follow that addon's repository documentation so hook and sidecar cleanup stay aligned with the addon package.

## Basic Commands

An uninstall without a platform argument is a full uninstall.

```bash
bash install.sh --uninstall
```

```powershell
.\install.cmd -Uninstall
```

```cmd
install.cmd -Uninstall
```

Specifying a platform cleans up only that platform.

```bash
bash install.sh --platform codex --uninstall
```

```powershell
.\install.cmd -Platform codex -Uninstall
```

To remove only selected skills, provide both the platform and skill names. If other Ghost-ALICE skills remain, hooks and `_shared` stay installed.

```bash
bash install.sh --platform codex --uninstall task-router
```

```powershell
.\install.cmd -Platform codex -Uninstall -Skills task-router
```

## Source Of Truth Files

Per-platform manifests live at:

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

Uninstall reads `targets` and `system_env_changes` from these manifests. If a manifest is missing or malformed, uninstall does not broaden into guessed deletion; it reports the item for manual review.

## Cleanup Order

1. Remove platform hook settings.
2. Remove Codex bootstrap or global-rule managed blocks.
3. Restore the source repository `core.hooksPath` only if Ghost-ALICE set it.
4. Remove only manifest targets that are inside allowed roots and verified by Ghost-ALICE ownership markers or hashes.
5. Remove support state such as install-state manifests, install-state events, Codex hook feature rollback metadata, pending merge queues, installed hook dispatcher assets, and install rollbacks.
6. Write a report under `~/.ghost-alice/uninstall-reports/`.

The uninstall report directory and privacy-sensitive local files such as `~/.ghost-alice/secrets.env` are preserved unless a future explicit purge mode is added. Runtime audit traces such as `~/.ghost-alice/io-trace.jsonl` are treated as local audit records rather than install targets.

## Recovery Principles

- Do not guess how to restore user settings that were not recorded.
- Do not automatically delete personal skills or user-owned files without markers.
- If another platform manifest references the same target, leave it in place and mark it `manual-review`.
- Missing files and already-cleared settings are reported as `missing` or `unchanged`, not as failures.

## system_env_changes

`system_env_changes` is an array of environment changes applied by the installer. Uninstall rolls back an entry only when that entry has `before`/`after` values or rollback metadata.

The current implementation emits `source_repo_hook_path`, `codex_hooks_feature_flag`, and `codex_project_trust`. The other `kind` values are reserved schema slots and are not active rollback coverage until their install-recording and uninstall-rollback tests exist.

- current: `source_repo_hook_path`
- current: `codex_hooks_feature_flag`
- current: `codex_project_trust`
- reserved: `ps_policy_change`, `posix_rc_change`, `macos_quarantine_fix`, `posix_chmod_fix`

When adding a new `kind`, update `installer_update/install_state_schema.md` first and add both install-recording and uninstall-rollback tests.
