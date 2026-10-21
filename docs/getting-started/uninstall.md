# Uninstall Cleanup Procedure

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/getting-started/uninstall.md)

Ghost-ALICE uninstall is driven by the `install-state` manifest written by the installer. The uninstall path removes or restores only entries that the Ghost-ALICE installer recorded as installed or changed.

## Contents

- [Choose The Removal Scope](#choose-the-removal-scope)
- [Basic Commands](#basic-commands)
- [Source Of Truth Files](#source-of-truth-files)
- [Cleanup Order](#cleanup-order)
- [Recovery Principles](#recovery-principles)
- [system_env_changes](#system_env_changes)

## Choose The Removal Scope

| Goal | Command path | Notes |
| --- | --- | --- |
| Remove all managed Ghost-ALICE surfaces | `bash install.sh --uninstall` | Full uninstall across recorded targets |
| Remove one platform | `bash install.sh --platform codex --uninstall` | Keeps other platform installs |
| Remove selected skills | `bash install.sh --platform codex --uninstall task-router` | Keeps hooks and shared assets when other skills remain |
| Remove Autopilot, retain Core | `bash install.sh --platform codex --uninstall --addon autopilot-mode` | Detaches owned executable hooks before removing verified addon files |

Addon-specific removal is different from selected skill removal. Core updates preserve existing addons; an explicit addon uninstall selects the removal. A full uninstall removes the active managed Ghost-ALICE footprint, including installed addon sidecars, while retaining original recovery material and local evidence. Per-addon removal keeps Core and other addons installed. Core skills required by an installed addon are protected by the existing dependency check during selected skill removal.

## Basic Commands

An uninstall without a platform argument is a full uninstall.

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

Specifying a platform cleans up only that platform.

```bash
bash install.sh --platform codex --uninstall
```

```cmd
.\install.cmd --platform codex --uninstall
```

To remove only selected skills, provide both the platform and skill names. If other Ghost-ALICE skills remain, hooks and `_shared` stay installed.

```bash
bash install.sh --platform codex --uninstall task-router
```

```cmd
.\install.cmd --platform codex --uninstall task-router
```

## Source Of Truth Files

Per-platform manifests live at:

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

Uninstall reads `targets` and `system_env_changes` from these manifests. If a manifest is missing or malformed, uninstall does not broaden into guessed deletion; it reports the item for manual review.

## Cleanup Order

The uninstall command performs these steps for the selected removal scope:

1. For full/platform removal, remove its installed addons first: write a removal-intent marker, detach exact owned addon and privileged adapter hook registrations, then remove only verified addon targets. A failed hook write leaves files and sidecar intact. Modified targets and their sidecar/removal marker remain for review or retry; detached hooks are not restored by that retry.
2. If a removal root is the registered context gateway's runtime (`~/.ghost-alice/runtime/current`, a folder under or above it) or one of its service files, or the removal is a Codex full uninstall, detach that gateway before changing Core hooks or files. The install record's `service` section names the service files. Confirm service absence, remove the owned launch agent and provider blocks, delete the `service` section, and preserve adjacent current settings, the state directory, component logs and existing recovery material. A symlinked Codex configuration or gateway service/state file, a service file that changed outside the installer, or an uncertain teardown stops dependent Core cleanup with a failure. A removal that leaves the service's code alone, such as a Claude removal or a selected Codex skill, keeps the gateway running; a link is judged by its own path, not by where it points. Without a `service` section the uninstall asks launchd: a loaded gateway service whose program is inside a removal root stops the cleanup for review.
3. Remove Core platform hook settings and Codex bootstrap or global-rule managed blocks, preserving user-owned entries.
4. Restore the source repository's shared `core.hooksPath` only in the full removal of the last host, only when the value is still the one the install-state records as written by the install, and only to the value it records from before the install (no value unsets the path). Another host's remaining install-state keeps the path for that host, and a value without such a record or changed since stays. Then apply the recorded feature/trust rollback conditions.
5. Remove only manifest targets inside allowed roots and verified by Ghost-ALICE ownership markers or hashes. Retain shared runtime and dispatcher assets when another platform still has an install-state manifest.
6. Archive the selected platform's pending merge originals before removing its active queue. Retain shared installation rollback copies while another platform is installed; otherwise archive them first. Each archive uses a new directory under `~/.ghost-alice/uninstall-backup/<platform>/support-state/`; its report item records `backup_path`. Copies preserve original modification times and symlink representations, and earlier archives remain intact. Before removing a recovery root, compare every archived root/nested symlink's modification time with its captured original. If link times cannot be preserved, retain the original root and its install-state manifest for review. Recovery material containing a Windows junction is likewise retained instead of being copied or deleted automatically.
7. Remove remaining installer support records, keeping the install-state manifest until support cleanup and recovery copying succeed. Write a report under `~/.ghost-alice/uninstall-reports/`; before it writes a report, that folder keeps its newest 30 reports.

The uninstall backup directories, the newest uninstall reports, source checkouts, conversation/session evidence, intent ledgers, and privacy-sensitive local files such as `~/.ghost-alice/secrets.env` are preserved. The install record `~/.ghost-alice/installed-payload.json` and the placement folders under `~/.ghost-alice/rollbacks/` are shared by both hosts: a full uninstall of one host keeps them for the other (`manual-review`, `shared-support-in-use-by-other-platform`), and the last host's full uninstall archives the placement folders under `~/.ghost-alice/uninstall-backup/<platform>/support-state/` and removes both. Runtime audit traces such as `~/.ghost-alice/io-trace.jsonl` are local audit records rather than install targets. No data purge is implied by removing executable integration. These file/configuration results do not establish ongoing-host adoption or cancellation of already delivered work. A preview without confirmation leaves install/configuration/recovery assets unchanged but writes its report, as the existing CLI does.

## Recovery Principles

Per-addon removal detaches Claude installer-added skill permissions and Codex owned hook trust before removing files. Claude permission ownership is bound to the settings file and manifest-selected skill names; preexisting grants, duplicate user grants and unknown legacy ownership remain protected. Codex surviving trust tables follow their actual group/handler positions, including repeated identical commands, with user fields and comments preserved. An unresolved destination collision stops removal. Config writes preserve symlinks and use atomic replacement; if the subsequent hook settings write fails, removal restores the original trust bytes and modification time. The original config backup remains available if restoration itself fails.

The addon removal CLI returns exit `2` for errors or partial removal, including resume errors. Exit `1` means the addon was not found. A retained modified target, unknown permission ownership or configuration failure does not count as complete removal.

- Do not guess how to restore user settings that were not recorded.
- Do not automatically delete personal skills or user-owned files without markers.
- If another platform manifest references the same target, leave it in place and mark it `manual-review`.
- Missing files and already-cleared settings are reported as `missing` or `unchanged`, not as failures.

## system_env_changes

`system_env_changes` is an array of environment changes applied by the installer. Uninstall rolls back an entry only when that entry has `before`/`after` values or rollback metadata.

The installer records `source_repo_hook_path`, `codex_hooks_feature_flag`, and `codex_project_trust`. Each has a corresponding uninstall handler and recorded rollback conditions.

When adding a new `kind`, update `installer_update/install_state_schema.md` first and add both install-recording and uninstall-rollback tests.
