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

The pending merge manifest is the quarantine for external changes and the queue for the human user's merge decisions. An external change is a change that the installer finds in an installed target after install and that its records do not hold; the installer does not record who made the change. This per-host inventory retains target baselines and environment rollback facts; the versioned shared installation record additionally records each publication and current host references. [Installer architecture](../docs/reference/installer-architecture.md#install-record) defines those distinct owners. The two manifests do not mix their paths or purposes.

## File location

The installer writes one manifest for the selected platform at the corresponding path below.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

## Installed ledger source relationships

The shared record’s optional `source.ledger_source_roots` uses normalized absolute ledger paths as keys. For each known host relationship, the canonical value contains the absolute `source_root` and the observed `hosts`, including when the source is the same checkout that is being installed. It is written in schema `3` under `installed-payload-v3.json`. The example shows one such relationship. A genuinely unattributed source path is a string. An empty map or a map of strings can use schema `2` under `installed-payload-v2.json`. A historical string alone cannot establish which host used the path.

```json
{
  "source": {
    "root": "/work/current-source",
    "ledger_source_roots": {
      "/work/current-source/session-intent": {
        "source_root": "/work/current-source",
        "hosts": ["claude"]
      }
    }
  }
}
```

`transaction.activate` writes the new `installer.active_root` in one update of the record before any host file changes; that update keeps the record's schema `2` or `3` form. Each placement, and the replacement of the recorded commands in `transaction.activate`, publishes the canonical relationships in the same step as its targets, and after every requested host succeeds `source.finalize_source` publishes them with the version success in one `record_update`. A `pending-install-source.json` that an earlier installer's interrupted activation left beside the installation record binds host names to that installer's active package, installation source and ledger paths. When those values still match the record, `transaction.activate` adds the host names in the same update as `installer.active_root`, and the record uses schema `3`; it deletes the file in every case. While v3 is active, notices at the v2 and legacy filenames prevent older writers from replacing it. Once no relationship requires host attribution, a later journaled update can publish v2 and retire the v3 detail. [Installer architecture](../docs/reference/installer-architecture.md#fixed-commands-policies-and-ledger-data)

The existing source writer merges fields by name. An omitted `ledger_source_roots` keeps the previous map; an explicit `{}` replaces it with an empty map. A valid map is stored with the new source fields in the same journaled update. Records without the optional field remain readable.

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
| `system_env_changes` | array | The installer records the user system environment changes it applied, except the Git hook path, which `~/.ghost-alice/git-hooks/repositories.json` records. An empty array records no such change. Uninstall uses each record and its kind-specific rollback conditions to restore the prior state. |

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

The installer writes `system_env_changes` entries for changes it applies to the user system environment, except the Git hook path (`source_repo_hook_path` below). Uninstall reads each entry and applies the rollback conditions documented for that kind before restoring the prior state.

Each entry is distinguished by the `kind` field. Uninstall has a corresponding handler for each of the following kinds.

| kind | meaning |
| --- | --- |
| `source_repo_hook_path` | The install source repository's shared `core.hooksPath` that an earlier installer wrote. `after` is the path it wrote; `before_present` and `before` give the value from before it, and `repo_root` names the install source. `source-repo-hook-path.json` next to the manifests holds the same fields. The installer keeps both and writes neither: `git_hooks.register` writes its rows to `~/.ghost-alice/git-hooks/repositories.json`, one per repository with the same fields and `common_dir`, the repository's Git common folder. Its first registration copies the earlier rows there; from then on the installer and uninstaller read only that file. Retirement of the final management consumer acts on each row: when the current value is `after` or, for a row with `common_dir`, another path inside `~/.ghost-alice`, it restores `before`. When there was no value before, or `before` is such a path, it unsets the value. Without a row the value stays. The removal of the final management consumer deletes `source-repo-hook-path.json` and `repositories.json`. |
| `codex_hooks_feature_flag` | A trace-backed record that Ghost-ALICE changed `~/.codex/config.toml` `[features] hooks` to `true`. Records `path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores `before_state` only from this record. |
| `codex_project_trust` | A trace-backed record that the installer changed `~/.codex/config.toml` `[projects."<repo>"] trust_level` to `trusted` for the Ghost-ALICE project. Records `path`, `project_path`, `before_state`, and `after_state` without storing raw config content. Uninstall restores or removes that trust entry only from this record. |

Each entry has an `applied_at` UTC ISO-8601 timestamp and the kind-specific fields above. Adding a kind requires both install-recording and uninstall-rollback implementation and tests.

The writer carries previous rollback records forward when the current run supplies none. A new record replaces only the same implemented owner: Codex feature configuration path, or Codex project path within its configuration file. `source_repo_hook_path` records and other recorded kinds are carried unchanged; the other kinds do not acquire a rollback handler.
