# install-state manifest schema

This document is the contract for the install-state manifest that the Ghost-ALICE installer records.
## Contents

- [Purpose](#purpose)
- [File location](#file-location)
- [top-level fields](#top-level-fields)
- [target fields](#target-fields)
- [install_mode semantics](#installmode-semantics)
- [managed_markers](#managedmarkers)
- [failure policy](#failure-policy)
- [system_env_changes](#systemenvchanges)


## Purpose

The install-state manifest is the baseline used to decide which artifacts the next install preflight and uninstall can treat as installer-owned.

The pending merge manifest is the quarantine for user changes and the merge decision queue. The install-state manifest is the SSOT for ownership and the install baseline. The two manifests do not mix their paths or purposes.

## File location

The per-platform manifest is placed at the following paths.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

## top-level fields

| field | type | meaning |
| --- | --- | --- |
| `schema_version` | integer | The current schema version. The initial version is `1`. |
| `platform` | string | One of `claude`, `codex`. |
| `installed_at` | string | The UTC ISO-8601 timestamp of when the manifest was recorded. |
| `source_root` | string | The normalized path of the install source repository root. |
| `source_branch` | string | The source branch at install time. A detached state is recorded as `DETACHED`. |
| `source_head` | string | The source HEAD commit at install time. When it cannot be determined, it is `unknown`. |
| `source_dirty_state` | string | One of `clean`, `dirty`, `unknown`. |
| `remote_freshness_state` | string | The remote freshness decision state. The initial implementation records `unverified`. |
| `targets` | array | The array of target records that the installer recorded as managed. |
| `system_env_changes` | array | The record of user system environment changes that the install self-applied. An empty array means no change or that it was not triggered. Uninstall reads this record to roll back to the pre-change state. |

## target fields

| field | type | meaning |
| --- | --- | --- |
| `target_name` | string | The name of the managed target. For example: `_shared`, `task-router`. |
| `source_path` | string | The source path that created the target. |
| `dest_path` | string | The installed destination path. |
| `install_mode` | string | One of `copy`, `copy-fallback`, `symlink`, `junction`, `wrapper`, `missing`. |
| `target_tree_hash` | string | The destination content baseline hash. A missing target is `missing`. |
| `managed_markers` | array | The list of markers that serve as a secondary way to identify target ownership. |
| `installed_at` | string | The UTC ISO-8601 timestamp of when that target record was recorded. |

## install_mode semantics

| mode | meaning |
| --- | --- |
| `copy` | A state where the installer deliberately chose copy mode to create the destination. |
| `copy-fallback` | A state where the path attempted a link or junction install, but the actual destination remained a plain copy. |
| `symlink` | A state installed as a POSIX-style symlink. |
| `junction` | A state installed as a Windows directory junction or reparse point. |
| `wrapper` | A platform wrapper artifact that is not part of the skill tree. |
| `missing` | A state where the destination could not be found at the time the manifest was recorded. Under the criteria for a normal completed install, it should not occur. |

## managed_markers

`managed_markers` is a value that uninstall and preflight can use as secondary evidence beyond the hash.

- A skill target records `SKILL.md` as its marker.
- The `_shared` target records `_shared` as its marker.

## failure policy

- If the manifest is absent, it is treated as a legacy install and the next preflight behaves conservatively.
- A manifest parse failure is not a silent success. It must be reported as a diagnostic failure.
- The manifest is not a recovery source for user changes. Recovery of user changes is handled by the pending merge manifest and the quarantine backup.
- When the manifest exists, installer doctor uses the `targets` array as the SSOT for that platform's managed targets. It does not fail merely because a catalog-wide skill that is absent from a partial-install manifest is missing.
- If a target recorded in the manifest has disappeared from its actual destination or fails to pass marker/hash verification, it is reported as an item requiring action.

## system_env_changes

`system_env_changes` is the array of changes that the install self-applied to the user system environment. Uninstall reads this record to roll back to the pre-change state.

Each entry is distinguished by the `kind` field. `source_repo_hook_path` and `codex_hooks_feature_flag` are emitted by the current implementation. The other kinds below are reserved schema slots and must not be documented as active until the install-recording and uninstall-rollback code for that kind lands with tests.

| kind | meaning |
| --- | --- |
| `source_repo_hook_path` | Current. A record of changing the install source repository's `core.hooksPath` to the Ghost-ALICE post-merge hook path. `repo_root`, `before_present`, `before`, and `after` are recorded. Uninstall restores to `before` only when the current value equals `after`, or unsets it when there was no previous value. |
| `codex_hooks_feature_flag` | Current. A trace-backed record that Ghost-ALICE changed `~/.codex/config.toml` `[features] hooks` to `true`. Records `path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores `before_state` only from this record. |
| `ps_policy_change` | Reserved. A future self-applied change of the Windows PowerShell ExecutionPolicy (CurrentUser scope). The `before`/`after` policy values and the `rollback_command` are recorded together when implemented. |
| `posix_rc_change` | Reserved. Future addition of a Ghost-ALICE managed block to a POSIX user shell rc file (`~/.bashrc`, `~/.zshrc`, `~/.profile`). `rc_path`, `block_marker`, and `added_lines` are recorded when implemented. |
| `macos_quarantine_fix` | Reserved. Future automatic removal of the macOS `com.apple.quarantine` xattr. The array of target paths is recorded in `target_paths` when implemented. |
| `posix_chmod_fix` | Reserved. Future self-restoration of POSIX execute bits (for example, files extracted from a ZIP). `target_paths`, `before_mode`, and `after_mode` are recorded when implemented. |

Each entry has an `applied_at` UTC ISO-8601 timestamp. Additional fields per kind (for example, `before`/`after`/`rollback_command` of `ps_policy_change`) are added to this schema document in the sub-phase that first uses that kind. This document is the SSOT for the system_env_changes definition.

The entry-building logic for reserved kinds and the doctor reader are added in later sub-phases while updating the same SSOT (this document).
