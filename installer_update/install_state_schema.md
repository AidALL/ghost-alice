# install-state manifest schema

This document is the contract for the install-state manifest that the Ghost-ALICE installer records.
## Contents

- [Purpose](#purpose)
- [File location](#file-location)
- [Installed ledger source relationships](#installed-ledger-source-relationships)
- [top-level fields](#top-level-fields)
- [target fields](#target-fields)
- [install_mode semantics](#install_mode-semantics)
- [managed_markers](#managed_markers)
- [failure policy](#failure-policy)
- [system_env_changes](#system_env_changes)


## Purpose

The install-state manifest is the baseline used to decide which artifacts the next install preflight and uninstall can treat as installer-owned.

The pending merge manifest is the quarantine for external changes and the queue for the human user's merge decisions. An external change is a change that the installer finds in an installed target after install and that its records do not hold; the installer does not record who made the change. This per-host inventory retains target baselines and environment rollback facts; the shared `installed-payload-v2.json` additionally records each publication and current host references. [Installer architecture](../docs/reference/installer-architecture.md#install-record) defines those distinct owners. The two manifests do not mix their paths or purposes.

## File location

The installer writes one manifest for the selected platform at the corresponding path below.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

## Installed ledger source relationships

The shared `~/.ghost-alice/installed-payload-v2.json` record has an optional `source.ledger_source_roots` object. This field belongs to the shared record, not the per-host manifest fields below. Each key is a normalized absolute installed ledger path; its value is the normalized absolute path of the source folder associated with that ledger. Both must be strings. A source folder need not still exist. `_shared/installed_payload.py` validates the map when reading or updating the shared record.

```json
{
  "source": {
    "root": "/work/current-source",
    "ledger_source_roots": {
      "/work/previous-source/session-intent": "/work/previous-source"
    }
  }
}
```

`source.root` identifies the current acquisition input; the map retains only the source relationships needed by current installed ledger bindings. It is neither a complete source history nor an instruction to select a ledger. `config.install_locations` and existing managed hooks supply the installed locations; source metadata transported by a request or saved package does not supply this map. The [installer architecture](../docs/reference/installer-architecture.md#fixed-commands-policies-and-ledger-data) owns relationship calculation and publication order.

The existing source writer merges fields by name. An omitted `ledger_source_roots` keeps the previous map; an explicit `{}` replaces it with an empty map. A valid map is stored with the new source fields in the same record replacement and journal. Records without the optional field remain readable.

## top-level fields

| field | type | meaning |
| --- | --- | --- |
| `schema_version` | integer | The current schema version is `1`. |
| `platform` | string | Host adapter identifier; the supplied adapters currently use `claude` and `codex`. |
| `installed_at` | string | The UTC ISO-8601 timestamp of when the manifest was recorded. |
| `source_root` | string | The normalized execution package root for the latest support publication. |
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
| `source_path` | string | The source path that supplied this target. Newly published Core targets use fixed packages; addons retain their own source paths. |
| `source_root` | string, optional | The source root that supplied this row. Unselected Core rows retain their earlier root when common support moves. Legacy and addon rows can omit it. |
| `dest_path` | string | The installed destination path. |
| `install_mode` | string | One of `copy`, `copy-fallback`, `symlink`, `junction`, `wrapper`, `missing`. |
| `target_tree_hash` | string | The destination content baseline hash. Directory records use `tree-v2:<sha256>`; readers compare historical unprefixed hashes with the original algorithm. A missing target is `missing`. |
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

New directory hashes frame each relative path and fixed-length file digest, so embedded separators in file contents cannot collide with file boundaries. Historical unprefixed hashes retain their original comparison semantics and limitations; a reader never replaces an old baseline by hashing the current disk.

## managed_markers

`managed_markers` is a value that uninstall and preflight can use as secondary evidence beyond the hash.

- A skill target records `SKILL.md` as its marker.
- The `_shared` target records `_shared` as its marker.

## failure policy

- If the manifest is absent, preflight treats ownership as unproven and behaves conservatively.
- If a consumer cannot parse the manifest, it reports a diagnostic failure rather than treating the manifest as valid.
- The manifest is not a recovery source for external changes. Recovery of external changes is handled by the pending merge manifest and the quarantine backup.
- When the manifest exists, installer doctor uses the `targets` array as the SSOT for that platform's managed targets. It does not fail merely because a catalog-wide skill that is absent from a partial-install manifest is missing.
- If a recorded target is missing from its destination or fails marker/hash verification, installer doctor reports that target as requiring action.

The writer retains unselected Core target rows during a partial reinstall. A historical failure-only object without `targets` migrates to an empty inventory; it provides no baseline. If the existing inventory cannot be read, installation preserves it under a unique sibling `.bak` name, reports the warning and writes the selected inventory. Partial-failure observations retain valid existing targets and rollback entries. Selected and addon removal prune only their completed destinations through `install_state_writer.remove_targets`.

## system_env_changes

The installer writes `system_env_changes` entries for changes it applies to the user system environment. Uninstall reads each entry and applies the rollback conditions documented for that kind before restoring the prior state.

Each entry is distinguished by the `kind` field. The installer records the following implemented changes; uninstall has a corresponding handler for each kind.

| kind | meaning |
| --- | --- |
| `source_repo_hook_path` | The install source repository's shared `core.hooksPath` that the install owns. `after` is the absolute path of the fixed package's `hooks` folder; `before_present` and `before` give the value from before Ghost-ALICE wrote one (the relative `hooks` an earlier installer wrote counts as none, unless that installer recorded for this repository the value it replaced), and `repo_root` names the install source. Every install that knows the earlier value writes the entry, so a reinstall that finds its own value in place carries the recorded `before` and `applied_at`; an install that finds a value no record names writes no entry. The git hook step also writes the same fields to `source-repo-hook-path.json` next to the manifests in the same step as the git write, so an install that stops before its manifest leaves the earlier value for the next install; the last host's full uninstall removes that file. Retirement of the final management consumer acts on it: when the current value equals `after`, it restores `before`, or unsets the value when there was none. Without this entry the value stays. |
| `codex_hooks_feature_flag` | A trace-backed record that Ghost-ALICE changed `~/.codex/config.toml` `[features] hooks` to `true`. Records `path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores `before_state` only from this record. |
| `codex_project_trust` | A trace-backed record that the installer changed `~/.codex/config.toml` `[projects."<repo>"] trust_level` to `trusted` for the Ghost-ALICE project. Records `path`, `project_path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores or removes that trust entry only from this record. |

Each entry has an `applied_at` UTC ISO-8601 timestamp and the kind-specific fields above. Adding a kind requires both install-recording and uninstall-rollback implementation and tests.

The writer carries previous rollback records forward when the current run supplies none. A new record replaces only the same implemented owner: source repository, Codex feature configuration path, or Codex project path within its configuration file. Other recorded kinds are carried unchanged and do not acquire a rollback handler.
