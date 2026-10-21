# install-state manifest schema

This document is the contract for the install-state manifest that the Ghost-ALICE installer records.
## Contents

- [Purpose](#purpose)
- [File location](#file-location)
- [top-level fields](#top-level-fields)
- [target fields](#target-fields)
- [install_mode semantics](#install_mode-semantics)
- [managed_markers](#managed_markers)
- [failure policy](#failure-policy)
- [system_env_changes](#system_env_changes)


## Purpose

The install-state manifest is the baseline used to decide which artifacts the next install preflight and uninstall can treat as installer-owned.

The pending merge manifest is the quarantine for user changes and the merge decision queue. The install-state manifest is the SSOT for ownership and the install baseline. The two manifests do not mix their paths or purposes.

## File location

The installer writes one manifest for the selected platform at the corresponding path below.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

## top-level fields

| field | type | meaning |
| --- | --- | --- |
| `schema_version` | integer | The current schema version is `1`. |
| `platform` | string | One of `claude`, `codex`. |
| `installed_at` | string | The UTC ISO-8601 timestamp of when the manifest was recorded. |
| `source_root` | string | The normalized path of the install source repository root. |
| `source_branch` | string | The source branch at install time. A detached state is recorded as `DETACHED`. |
| `source_head` | string | The source HEAD commit at install time. When it cannot be determined, it is `unknown`. |
| `source_dirty_state` | string | One of `clean`, `dirty`, `unknown`. |
| `remote_freshness_state` | string | The remote freshness decision state. The installer records `unverified`. |
| `targets` | array | The array of target records that the installer recorded as managed. |
| `system_env_changes` | array | The installer records the user system environment changes it applied. An empty array records no such change. Uninstall uses each record and its kind-specific rollback conditions to restore the prior state. |

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
| `copy-fallback` | The installer attempted a link or junction install, but created or retained a plain copy at the destination. |
| `symlink` | A state installed as a POSIX-style symlink. |
| `junction` | A state installed as a Windows directory junction or reparse point. |
| `wrapper` | A platform wrapper artifact that is not part of the skill tree. |
| `missing` | A state where the destination could not be found at the time the manifest was recorded. Under the criteria for a normal completed install, it should not occur. |

## managed_markers

`managed_markers` is a value that uninstall and preflight can use as secondary evidence beyond the hash.

- A skill target records `SKILL.md` as its marker.
- The `_shared` target records `_shared` as its marker.

## failure policy

- If the manifest is absent, preflight treats ownership as unproven and behaves conservatively.
- If a consumer cannot parse the manifest, it reports a diagnostic failure rather than treating the manifest as valid.
- The manifest is not a recovery source for user changes. Recovery of user changes is handled by the pending merge manifest and the quarantine backup.
- When the manifest exists, installer doctor uses the `targets` array as the SSOT for that platform's managed targets. It does not fail merely because a catalog-wide skill that is absent from a partial-install manifest is missing.
- If a recorded target is missing from its destination or fails marker/hash verification, installer doctor reports that target as requiring action.

## system_env_changes

The installer writes `system_env_changes` entries for changes it applies to the user system environment. Uninstall reads each entry and applies the rollback conditions documented for that kind before restoring the prior state.

Each entry is distinguished by the `kind` field. The installer records the following implemented changes; uninstall has a corresponding handler for each kind.

| kind | meaning |
| --- | --- |
| `source_repo_hook_path` | The install source repository's shared `core.hooksPath` that the install owns. `after` is the absolute path of the install source's `hooks` folder; `before_present` and `before` give the value from before Ghost-ALICE wrote one (the relative `hooks` an earlier installer wrote counts as none), and `repo_root` names the install source. Every install that knows the earlier value writes the entry, so a reinstall that finds its own value in place carries the recorded `before` and `applied_at`; an install that finds a value no install-state names writes no entry. Only the full uninstall of the last host acts on it: when the current value equals `after`, it restores `before`, or unsets the value when there was none. Without this entry the value stays. |
| `codex_hooks_feature_flag` | A trace-backed record that Ghost-ALICE changed `~/.codex/config.toml` `[features] hooks` to `true`. Records `path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores `before_state` only from this record. |
| `codex_project_trust` | A trace-backed record that Ghost-ALICE changed `~/.codex/config.toml` `[projects."<repo>"] trust_level` to `trusted`. Records `path`, `project_path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores or removes that trust entry only from this record. |

Each entry has an `applied_at` UTC ISO-8601 timestamp and the kind-specific fields above. Adding a kind requires both install-recording and uninstall-rollback implementation and tests.
