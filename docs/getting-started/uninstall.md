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

To remove only selected skills, provide both the platform and skill names. If the install record still names another Core skill of that platform, hooks and `_shared` stay installed, also while that skill's folder is missing.

Selected removal expands family names to their actual leaves and checks dependencies on both the family and each leaf. The uninstaller classifies the requested targets and asks the gateway participant to detach a service only when the physical removal affects it; removing discovery skills leaves an independent gateway runtime running. When the requested host will have no Core skill, the uninstaller first preserves required record-source references through the [retention owner](../reference/installer-architecture.md#fixed-commands-policies-and-ledger-data), then detaches Core hooks when retention permits and checks the saved settings before deleting files. It rechecks ownership, removes eligible files or releases this host's confirmed shared references, and updates the [removal records](../reference/installer-architecture.md#removal-records). Another host's retained files do not count as this host's remaining Core; unresolved ownership does. The uninstaller removes the bootstrap only when no Core skill remains and then checks current-host addons, services and other hosts' recorded references. When the final actual consumer is gone, it retires management commands, the common runtime and the active CLI binding. Saved code and ledger data remain. Selected removal leaves retired-source cleanup to full removal or the record-movement follow-up. [Recovery principles](#recovery-principles) describe failures after detachment.

When the last Core skill is removed, the uninstaller uses the full-removal handlers to restore the Codex hook setting and project trust changed during installation. It preserves later user edits and settings needed by other hooks. If restoration fails, it keeps the installation record and reports the cause.

Both removal commands leave personal files in the hooks directory. They retain the installed hook dispatcher when a remaining hook command contains its full path. If a directory has replaced a managed event file, they keep its contents and identify its path for review.

```bash
bash install.sh --platform codex --uninstall task-router
```

```cmd
.\install.cmd --platform codex --uninstall task-router
```

## Source Of Truth Files

The uninstaller reads the current work-record locations and their confirmed installation-source relationships before detaching hooks and removing installation records, then includes those locations with the removal result. It reads configuration and actual managed hooks separately, so a configured new location cannot hide a hook that still names the old location. It preserves the current work-record location and `config.json` ledger settings even when no cleanup journal exists. A completed move's retired original folder is handled separately under [Cleanup order](#cleanup-order). Records that have not moved remain dependent on their old installation source folder; see [moving existing work records](./installation.md#move-existing-work-records) before moving or deleting it. Full removal can delete the installation record that held the old source relationship while preserving the current data and configured path. A later installation from another folder may therefore report an unknown relationship instead of offering a move; that missing relationship does not mean the work records were deleted.

When configuration is unreadable, the normal removal command still identifies an observed path as fixed if it matches the fixed record location. Other paths found in managed hooks remain observed paths whose current or retired status is unconfirmed. A malformed entry for an inactive other host does not make the requested host's removal fail. The normal command's warning identifies that host, configuration path and cause; a whole-configuration error or a dependency needed by the requested removal still affects its result. Standalone cleanup applies the same dependency rules but does not display these informational location warnings.

Per-platform manifests live at:

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

Uninstall reads `targets` and `system_env_changes` from the host manifest. When the manifest is absent, full removal and final selected removal check this host’s hooks, payload references, addons, host-specific support paths and use of shared items. If the hooks are detached and no work remains for this host, both commands return exit `0` without a synthetic missing-manifest row or retry prompt. A finished journal for this host’s inventory can still require cleanup. On the last host an empty rollbacks root can also be removed. A malformed manifest, unresolved ownership or remaining host-specific paths are reported for review with the paths and a nonzero exit code. An inventory that still lists targets is preserved for retry. Removing selected skills preserves the runtime and installation records that a separately registered context gateway uses. When an earlier installation has no payload record, removal checks the other host’s live targets and hook registrations before deleting the shared runtime. Unknown ownership or unreadable settings keep that runtime available for retry.

## Cleanup Order

Before these steps, the normal removal command and standalone cleanup `--apply` both acquire the existing installation lock and recover unfinished publishes. An inherited installation lock reuses its parent's completed recovery. When recovery fails, the removal stage stops before changing installation targets or hook settings and reports the affected path and cause.

Full uninstall performs these steps for each selected platform:

1. Capture the command-start JSON/TOML settings bytes, ask each owner to classify its targets, and build one removal-item list. Before detaching hooks, pass the observed record locations to the [record-source retention owner](../reference/installer-architecture.md#fixed-commands-policies-and-ledger-data), which saves the host scope needed for pending retired-source cleanup in existing journals and returns host-scoped detachment blocks separately from shared observation or journal errors. Report shared errors and the requested host's blocking causes. Keep the affected host's registrations when detachment would lose a required reference; a reference preserved in surviving configuration does not alone block detachment. Check the gateway's removal preconditions under its existing lock before changing settings or files. This check does not stop the service or detach its configuration.
2. Complete a valid pending permission change for its recorded owner and original skill names before applying another owner's settings changes. Detach owned addon registrations and Core registrations whose reference retention permits removal. The Core permission writer and hook editor save one combined settings candidate. Permission ownership that cannot be established preserves those rules; it does not invalidate independent hook or file ownership.
3. Read the actual hook settings after the detach attempts. When hook detachment is blocked or unconfirmed, mark the affected skills, runtime and retry records for retention in the same removal list. Continue independent target removal and report its results on screen and in the JSON report; a retained dependency is not a successful removal. A record-reference, settings write or read error remains an error even when independent cleanup can continue. Then restore Codex feature and project-trust settings under their recorded conditions.
4. The uninstaller passes the remaining removal roots and whether Codex full removal was requested to `installer.gateway_participant.removal`. That participant decides whether to detach the registered context gateway before the uninstaller deletes files. The install record's `service` section names the service files. Confirm service absence, remove the owned launch agent and provider blocks, delete the `service` section, and preserve adjacent current settings, the state directory, component logs and existing recovery material. A symlinked Codex configuration or gateway service/state file, a service file that changed outside the installer, or an uncertain teardown stops dependent Core cleanup with a failure. A removal that retains the runtime and service files and does not request Codex disconnection keeps the gateway running; a link is judged by its own path, not by where it points. Without a `service` section the uninstall asks launchd: a loaded gateway service whose program is inside a removal root stops the cleanup for review.
5. Recheck ownership immediately before applying each remaining target item. Remove only confirmed managed targets inside allowed roots. When another host has a valid reference in its inventory, current payload or addon records, preserve shared files and release the requested host's reference only after confirming that this host no longer needs them. A held hook or unresolved removal keeps its required management/runtime references even when another host also uses those files. Pass each confirmed result to the [record owners](../reference/installer-architecture.md#removal-records); preserve failed targets' retry evidence before support cleanup.
6. Remove Codex bootstrap or global-rule managed blocks while preserving entries Ghost-ALICE does not manage. Restore the shared `core.hooksPath` of each repository in the hook-path record (`~/.ghost-alice/git-hooks/repositories.json`; the install-state while that file does not exist) when no host target, addon or service still needs the management commands, including after selected removal, only when the value is still one that the install wrote (the value in the record or, for a row with `common_dir`, another path inside `~/.ghost-alice`), and only to the value it records from before. Without such a value, or when that value is a path inside `~/.ghost-alice`, the uninstaller unsets the path. Another host's confirmed live references keep the path for that host; an empty inventory alone does not. Unavailable reference observations retain the hook setting and its recovery record with an error. A value without a change record or changed since stays.
7. Ask the [final cleanup owner](../reference/installer-architecture.md#retirement-and-final-cleanup) to delete confirmed unnecessary legacy copies before archiving the remaining recovery material. For completed record moves, the last host's full uninstall also checks the old folder against its verified cleanup baseline and current host/service use, removes unchanged remaining files and reports the deleted path. Changed or additional files, live use or an unavailable observation keep the affected original and cleanup evidence; the result names the remaining path and cause. Keep pending source-cleanup journals in active `rollbacks` while their originals remain, so the next attempt can read the saved host scope. Deferred cleanup lists the remaining original and journal paths. Failed cleanup reports the cause and the record locations available from its observations; the report also identifies the retained rollback folder. The new record location is preserved. Archive the selected platform's pending merge originals before removing its active queue; keep an active queue needed by an incomplete removal. Retain shared rollback copies while another host uses them. Each archive uses a new directory under `~/.ghost-alice/uninstall-backup/<platform>/support-state/`, reported as `backup_path`. If deletion of the original fails after the copy finishes, the screen and report identify that copy. Preserve original modification times and symlink representations, including every root/nested link. If link times cannot be preserved, or the recovery material contains a Windows junction, retain the original root and its install-state manifest for review.
8. Remove remaining support records only when they are no longer needed for retry. Keep the install-state manifest until the preceding record cleanup and recovery copying succeed. A failed settings or source Git hook-path restoration also retains its change record and the manifest. A failed hook-path restoration also retains the files in `~/.ghost-alice/git-hooks`. Otherwise the removal of the final consumer deletes only `post-merge` and `repositories.json` from that folder and leaves its other files. Write a report under `~/.ghost-alice/uninstall-reports/`; [runtime state retention](../reference/repository-structure.md#runtime-state-retention) sets how many reports that folder keeps.

The uninstall does not remove the uninstall backup directories, the newest uninstall reports, source checkouts, conversation/session evidence, records at the current ledger location, or privacy-sensitive local files such as `~/.ghost-alice/secrets.env`. That does not keep them forever: while Ghost-ALICE is installed, the [runtime state retention](../reference/repository-structure.md#runtime-state-retention) rules apply to them separately. The shared installation record, including its version notices when present, `post-merge` and `repositories.json` in `~/.ghost-alice/git-hooks/` and the placement folders under `~/.ghost-alice/rollbacks/` are shared by both hosts: a full uninstall of one host keeps them for the other (`manual-review`, `shared-support-in-use-by-other-platform`). The last host's full uninstall asks the [completed-placement cleanup owner](../reference/installer-architecture.md#successful-code-versions) to delete unnecessary managed originals and then applies the retention and removal conditions in steps 7–8 to the remaining support paths. The rollbacks support row reports the root’s actual location or removal after the final command transaction. A recorded runtime with only missing files can finish removal on retry. Changed or added content is preserved for review. Runtime audit traces such as `~/.ghost-alice/io-trace.jsonl` are local audit records rather than install targets. No data purge is implied by removing executable integration. These file/configuration results do not establish ongoing-host adoption or cancellation of already delivered work.

The uninstaller retains saved code versions in `~/.ghost-alice/versions/` even after the last host's full removal. A later successful Core installation prunes those versions under the [successful code version policy](../reference/installer-architecture.md#successful-code-versions); uninstall does not run that version pruning.

After deleting the development clone, use `~/.ghost-alice/bin/ghost-alice --uninstall` (Windows: `%USERPROFILE%\.ghost-alice\bin\ghost-alice.cmd --uninstall`). Once final-consumer removal retires that command, a saved package's installer can reinstall the code: with `--local-source` and no host it installs the Core selection that the package saved for each host, and no addon. A file or record deletion failure reports the remaining target and cause and does not undo successful hook or service detachment.

## Recovery Principles

When reading cleanup reports, treat schema version `2`, `mode=apply` and `<platform>-apply-<stamp>.json` as the applied-removal output. Historical version `1` reports used `mode=confirm` and `<platform>-confirm-<stamp>.json` for that same execution mode; neither name records a human confirmation. The standalone cleanup CLI uses `--apply` and retains `--confirm` as an alias. Without either flag it leaves install/configuration/recovery assets unchanged and writes a preview report. Addon removal applies by default, and its `--dry-run` takes precedence over either positive alias.

The live classifier writes `externally-modified-managed` for a managed target with an external change: a change after install that the installer's records do not hold, whose writer the installer does not record. It writes `unclaimed` when no management record claims the entry; that state does not permit deletion. When reading historical reports or pending entries, interpret `user-modified-managed` as `externally-modified-managed`, `user-owned` and diagnostic `owner=user` as `unclaimed`, `user-modified-purge` as `external-change-purge`, `user-modified-quarantine` as `external-change-quarantine`, and `retired-managed-user-changes-preserved` as `retired-managed-external-changes-preserved`. These names describe classification and preservation, not who changed a file. Do not rewrite historical entries, backup paths, hashes or recorded outcomes merely to update their names; removal and doctor classify the live targets again.

Per-addon removal detaches the `Skill(...)` allow rules the installer added to Claude Code settings and Codex owned hook trust before removing files. The installer's ownership of a Claude Code allow rule is bound to the settings file and manifest-selected skill names; preexisting rules, later duplicate rules that this installer did not add and legacy rules with unknown ownership remain protected. Surviving Codex hook trust entries follow their actual group/handler positions, including repeated identical commands. The installer preserves the entries, fields and comments of hooks Ghost-ALICE did not install; it does not record who wrote them. An unresolved destination collision stops removal. When a settings writer creates a `.bak`, it uses the bytes captured at the start of the whole uninstall command, including addon, Core and gateway changes. Each writer's immediate rollback still restores the state before its own write. Config writes preserve symlinks and use atomic replacement; if the subsequent hook settings write fails, removal restores the original trust bytes and modification time. The original config backup remains available if restoration itself fails.

The installer's Claude permission writer stores attribution in `addon-permissions/claude-v2.json` (schema `2`). It records Core additions separately from addon additions in this file. Full hook removal removes one occurrence of each recorded Core addition and retires its removal record; it preserves preexisting rules, later duplicates and legacy Core rules without addition records. The last case produces a warning naming the preserved rules. The writer also reads addon records without the optional Core fields. An older schema-2 reader that rejects these additional fields cannot use the new record for removal and reports an error instead of deleting rules.

When the new permission record is absent, the writer reads the legacy `claude.json` record. To save, it stages the new attribution, moves any legacy record to a unique `claude-v1-*.bak` name, then commits the active v2 record as its last file operation. Empty attribution removes the active record instead. If that final operation fails, the writer restores the legacy record; if restoration also fails, it retains the backup and reports both errors. Before removing a rule, the uninstaller records pending-detach fingerprints. If the uninstaller saves settings but fails to save attribution afterward, the next removal attempt identifies the applied change from the settings fingerprint and finishes saving attribution without removing another duplicate rule. If the current settings fingerprint matches neither the before nor the after fingerprint, the uninstaller leaves the item for review.

An older uninstaller that reads only `claude.json` cannot use ownership recorded in `claude-v2.json`. After a successful migration moves the legacy file to a backup, a remaining addon allow rule without ownership evidence receives `manual-review`; the older uninstaller preserves the rule and returns `partial` before removing addon files. Reinstalling the addon with the older installer can recreate `claude.json` and classify the still-present rule as preexisting (`False`). A later uninstall with the older version can then preserve that rule as `preserved-existing` while removing addon files if the other removal checks pass. An older uninstaller's file-removal result therefore does not establish that it removed permissions attributed by the v2 record. The [platform update behavior](./installation.md#platform-update-behavior) describes the separate intent-ledger limitation when returning to older code.

The addon removal CLI returns exit `2` for errors or partial removal, including resume errors. Exit `1` means the addon was not found. A retained modified target, unknown permission ownership or configuration failure does not count as complete removal.

If the uninstaller cannot preserve a required record reference before hook detachment, it keeps that host's hooks and reports the affected path and the cause. If ownership rechecking, file deletion or record cleanup fails after hooks or a related service were detached, the uninstaller keeps the successful detachment. It reports which files remain, which deleted files are absent and which records still need cleanup, preserves their retry evidence and returns a nonzero exit code. In an addon removal result, `unknown` means the uninstaller could not inspect the file's state; it does not mean that the file was deleted. In either case, resolve the reported cause before running the same removal command again. The uninstaller rechecks the current state and resumes the remaining removal or record cleanup without reinstalling or reconnecting anything. File deletion alone does not establish completed removal.

Full removal returns exit `0` when the only unresolved work is reviewing permission ownership: it preserves those permissions, the retained addons' files and their declared Core dependencies, removes independently owned targets, and displays `partial: permissions retained for review`. Confirmed sharing and user material selected for preservation likewise remain preserved; their presence alone is not a required deletion failure. A modified addon target or an actual settings, Git restoration, gateway or required cleanup failure returns a nonzero exit code. The full-removal permission exception does not change the addon removal CLI's exit contract. The removal screens show a required target failure with its path and cause. A permission retained for review receives the retained-item message.

- Confirmed references from another host preserve the shared target while the requested host's reference is released.
- Missing files and already-cleared settings are reported as `missing` or `unchanged`; any outstanding record cleanup must still finish.

## system_env_changes

`system_env_changes` is an array of environment changes applied by the installer. Uninstall rolls back an entry only when that entry has `before`/`after` values or rollback metadata.

The installer records `codex_hooks_feature_flag` and `codex_project_trust` there, and `source_repo_hook_path` in `~/.ghost-alice/git-hooks/repositories.json`. Each has a corresponding uninstall handler and recorded rollback conditions. The handler restores a Codex setting only when its current value matches the value recorded after installation; it preserves a different current value. Step 6 above gives the conditions for `source_repo_hook_path`. A configuration read or syntax error retains the change record and reports the error.

When adding a new `kind`, update `installer_update/install_state_schema.md` first and add both install-recording and uninstall-rollback tests.
