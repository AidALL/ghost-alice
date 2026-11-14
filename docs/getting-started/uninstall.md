# Uninstall Cleanup Procedure

[🇺🇸 English](./uninstall.md) | [🇰🇷 한국어](../ko/getting-started/uninstall.md)

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
| Remove one addon, retain Core | `bash install.sh --platform codex --uninstall --addon <addon-id>` | Detaches the addon's hooks before removing its verified files |

Addon-specific removal is different from selected skill removal. Core updates preserve existing addons; an explicit addon uninstall selects the removal. A full uninstall removes the active managed Ghost-ALICE footprint, including installed addon sidecars, while retaining needed recovery material and local evidence. Per-addon removal keeps Core and other addons installed. Core skills required by an installed addon are protected by the existing dependency check during selected skill removal.

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

Selected removal expands family names to their actual leaves and checks dependencies on both the family and each leaf. The uninstaller classifies the requested targets and asks the gateway participant to detach a service only when the physical removal affects it; removing discovery skills leaves an independent gateway runtime running. When the requested host will have no Core skill, the uninstaller detaches Core hooks and checks the saved settings before deleting files. It rechecks ownership, removes eligible files or releases this host's confirmed shared references, and updates the [removal records](../reference/installer-architecture.md#removal-records). Another host's retained files do not count as this host's remaining Core; unresolved ownership does. The uninstaller removes the bootstrap only when no Core skill remains and retains the support records covered by full removal below. [Recovery principles](#recovery-principles) describe failures after detachment.

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

Uninstall reads `targets` and `system_env_changes` from these manifests. If a manifest is missing or malformed, the uninstaller reports it for human review and preserves support records and recovery material that would otherwise be removed. Independent hook cleanup still reports its own result; that result does not establish that manifest targets were removed.

## Cleanup Order

Full uninstall performs these steps for each selected platform:

1. Capture the command-start JSON/TOML settings bytes, ask each owner to classify its targets, and build one removal-item list. Check the gateway's removal preconditions under its existing lock before changing settings or files. This check does not stop the service or detach its configuration.
2. Complete a valid pending permission change for its recorded owner and original skill names before applying another owner's settings changes. Detach owned addon registrations and Core registrations. The Core permission writer and hook editor save one combined settings candidate. Permission ownership that cannot be established preserves those rules; it does not invalidate independent hook or file ownership.
3. Read the actual hook settings after the detach attempts. When hook detachment is unconfirmed, mark the affected skills, runtime and retry records for retention in the same removal list. A retained dependency is not a successful removal. A settings write or read error remains an error even when independent cleanup can continue. Then restore Codex feature and project-trust settings under their recorded conditions.
4. The uninstaller passes the remaining removal roots and whether Codex full removal was requested to `installer.gateway_participant.removal`. That participant decides whether to detach the registered context gateway before the uninstaller deletes files. The install record's `service` section names the service files. Confirm service absence, remove the owned launch agent and provider blocks, delete the `service` section, and preserve adjacent current settings, the state directory, component logs and existing recovery material. A symlinked Codex configuration or gateway service/state file, a service file that changed outside the installer, or an uncertain teardown stops dependent Core cleanup with a failure. A removal that retains the runtime and service files and does not request Codex disconnection keeps the gateway running; a link is judged by its own path, not by where it points. Without a `service` section the uninstall asks launchd: a loaded gateway service whose program is inside a removal root stops the cleanup for review.
5. Recheck ownership immediately before applying each remaining target item. Remove only confirmed managed targets inside allowed roots. When another host has a valid reference in its inventory, current payload or addon records, preserve shared files and release only the requested host's reference. Pass each confirmed result to the [record owners](../reference/installer-architecture.md#removal-records); preserve failed targets' retry evidence before support cleanup.
6. Remove Codex bootstrap or global-rule managed blocks while preserving entries Ghost-ALICE does not manage. Restore the source repository's shared `core.hooksPath` only in the full removal of the last host, only when the value is still the one the install-state records as written by the install, and only to the value it records from before the install (no value unsets the path). Another host's remaining install-state keeps the path for that host, and a value without such a record or changed since stays.
7. Ask the [final cleanup owner](../reference/installer-architecture.md#retirement-and-final-cleanup) to delete confirmed unnecessary legacy copies before archiving the remaining recovery material. Archive the selected platform's pending merge originals before removing its active queue; keep an active queue needed by an incomplete removal. Retain shared rollback copies while another host uses them. Each archive uses a new directory under `~/.ghost-alice/uninstall-backup/<platform>/support-state/`, reported as `backup_path`. Preserve original modification times and symlink representations, including every root/nested link. If link times cannot be preserved, or the recovery material contains a Windows junction, retain the original root and its install-state manifest for review.
8. Remove remaining support records only when they are no longer needed for retry. Keep the install-state manifest until the preceding record cleanup and recovery copying succeed. A failed settings or source Git hook-path restoration also retains its change record and the manifest. Write a report under `~/.ghost-alice/uninstall-reports/`; [runtime state retention](../reference/repository-structure.md#runtime-state-retention) sets how many reports that folder keeps.

The uninstall does not remove the uninstall backup directories, the newest uninstall reports, source checkouts, conversation/session evidence, intent ledgers, or privacy-sensitive local files such as `~/.ghost-alice/secrets.env`. That does not keep them forever: while Ghost-ALICE is installed, the [runtime state retention](../reference/repository-structure.md#runtime-state-retention) rules apply to them separately. The install record `~/.ghost-alice/installed-payload.json` and the placement folders under `~/.ghost-alice/rollbacks/` are shared by both hosts: a full uninstall of one host keeps them for the other (`manual-review`, `shared-support-in-use-by-other-platform`). The last host's full uninstall asks the [completed-placement cleanup owner](../reference/installer-architecture.md#successful-code-versions) to delete unnecessary managed originals before archiving the remaining placement recovery material under `~/.ghost-alice/uninstall-backup/<platform>/support-state/` and removing both active paths. Runtime audit traces such as `~/.ghost-alice/io-trace.jsonl` are local audit records rather than install targets. No data purge is implied by removing executable integration. These file/configuration results do not establish ongoing-host adoption or cancellation of already delivered work.

The uninstaller retains saved code versions in `~/.ghost-alice/versions/` even after the last host's full removal. A later full Core installation prunes those versions under the [successful code version policy](../reference/installer-architecture.md#successful-code-versions); uninstall does not run that version pruning.

## Recovery Principles

When reading cleanup reports, treat schema version `2`, `mode=apply` and `<platform>-apply-<stamp>.json` as the applied-removal output. Historical version `1` reports used `mode=confirm` and `<platform>-confirm-<stamp>.json` for that same execution mode; neither name records a human confirmation. The standalone cleanup CLI uses `--apply` and retains `--confirm` as an alias. Without either flag it leaves install/configuration/recovery assets unchanged and writes a preview report. Addon removal applies by default, and its `--dry-run` takes precedence over either positive alias.

The live classifier writes `externally-modified-managed` for a managed target with an external change: a change after install that the installer's records do not hold, whose writer the installer does not record. It writes `unclaimed` when no management record claims the entry; that state does not permit deletion. When reading historical reports or pending entries, interpret `user-modified-managed` as `externally-modified-managed`, `user-owned` and diagnostic `owner=user` as `unclaimed`, `user-modified-purge` as `external-change-purge`, `user-modified-quarantine` as `external-change-quarantine`, and `retired-managed-user-changes-preserved` as `retired-managed-external-changes-preserved`. These names describe classification and preservation, not who changed a file. Do not rewrite historical entries, backup paths, hashes or recorded outcomes merely to update their names; removal and doctor classify the live targets again.

Per-addon removal detaches the `Skill(...)` allow rules the installer added to Claude Code settings and Codex owned hook trust before removing files. The installer's ownership of a Claude Code allow rule is bound to the settings file and manifest-selected skill names; preexisting rules, later duplicate rules that this installer did not add and legacy rules with unknown ownership remain protected. Surviving Codex hook trust entries follow their actual group/handler positions, including repeated identical commands. The installer preserves the entries, fields and comments of hooks Ghost-ALICE did not install; it does not record who wrote them. An unresolved destination collision stops removal. When a settings writer creates a `.bak`, it uses the bytes captured at the start of the whole uninstall command, including addon, Core and gateway changes. Each writer's immediate rollback still restores the state before its own write. Config writes preserve symlinks and use atomic replacement; if the subsequent hook settings write fails, removal restores the original trust bytes and modification time. The original config backup remains available if restoration itself fails.

The installer's Claude permission writer stores attribution in `addon-permissions/claude-v2.json` (schema `2`). It records Core additions separately from addon additions in this file. Full hook removal removes one occurrence of each recorded Core addition and retires its removal record; it preserves preexisting rules, later duplicates and legacy Core rules without addition records. The last case produces a warning naming the preserved rules. The writer also reads addon records without the optional Core fields. An older schema-2 reader that rejects these additional fields cannot use the new record for removal and reports an error instead of deleting rules.

When the new permission record is absent, the writer reads the legacy `claude.json` record. To save, it stages the new attribution, moves any legacy record to a unique `claude-v1-*.bak` name, then commits the active v2 record as its last file operation. Empty attribution removes the active record instead. If that final operation fails, the writer restores the legacy record; if restoration also fails, it retains the backup and reports both errors. Before removing a rule, the uninstaller records pending-detach fingerprints. If the uninstaller saves settings but fails to save attribution afterward, the next removal attempt identifies the applied change from the settings fingerprint and finishes saving attribution without removing another duplicate rule. If the current settings fingerprint matches neither the before nor the after fingerprint, the uninstaller leaves the item for review.

An older uninstaller that reads only `claude.json` cannot use ownership recorded in `claude-v2.json`. After a successful migration moves the legacy file to a backup, a remaining addon allow rule without ownership evidence receives `manual-review`; the older uninstaller preserves the rule and returns `partial` before removing addon files. Reinstalling the addon with the older installer can recreate `claude.json` and classify the still-present rule as preexisting (`False`). A later uninstall with the older version can then preserve that rule as `preserved-existing` while removing addon files if the other removal checks pass. An older uninstaller's file-removal result therefore does not establish that it removed permissions attributed by the v2 record. The [platform update behavior](./installation.md#platform-update-behavior) describes the separate intent-ledger limitation when returning to older code.

The addon removal CLI returns exit `2` for errors or partial removal, including resume errors. Exit `1` means the addon was not found. A retained modified target, unknown permission ownership or configuration failure does not count as complete removal.

If ownership rechecking, file deletion or record cleanup fails after hooks or a related service were detached, the uninstaller keeps the successful detachment. It reports which files remain, which deleted files are absent and which records still need cleanup, preserves their retry evidence and returns a nonzero exit code. In an addon removal result, `unknown` means the uninstaller could not inspect the file's state; it does not mean that the file was deleted. After resolving the reported cause, run the same removal command again: it rechecks current state and finishes the remaining deletion or record cleanup without reinstalling or reconnecting anything. File deletion alone does not establish completed removal.

Full removal returns exit `0` when the only unresolved work is reviewing permission ownership: it preserves those permissions, the retained addons' files and their declared Core dependencies, removes independently owned targets, and displays `partial: permissions retained for review`. Confirmed sharing and user material selected for preservation likewise remain preserved; their presence alone is not a required deletion failure. A modified addon target or an actual settings, Git restoration, gateway or required cleanup failure returns a nonzero exit code. The full-removal permission exception does not change the addon removal CLI's exit contract.

- Confirmed references from another host preserve the shared target while the requested host's reference is released.
- Missing files and already-cleared settings are reported as `missing` or `unchanged`; any outstanding record cleanup must still finish.

## system_env_changes

`system_env_changes` is an array of environment changes applied by the installer. Uninstall rolls back an entry only when that entry has `before`/`after` values or rollback metadata.

The installer records `source_repo_hook_path`, `codex_hooks_feature_flag`, and `codex_project_trust`. Each has a corresponding uninstall handler and recorded rollback conditions. The handler restores a setting only when its current value matches the value recorded after installation; it preserves a different current value. A configuration read or syntax error retains the change record and reports the error.

When adding a new `kind`, update `installer_update/install_state_schema.md` first and add both install-recording and uninstall-rollback tests.
