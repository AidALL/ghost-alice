# Repository Structure

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/repository-structure.md)

This document is the repository map. The README stays a concise public entry point; this page carries the detailed structure.
## Contents

- [Top-Level Documents](#top-level-documents)
- [Installer And Platforms](#installer-and-platforms)
- [Shared Utilities](#shared-utilities)
- [Core Governance And Runtime Gates](#core-governance-and-runtime-gates)
- [Official Docs And Policies](#official-docs-and-policies)
- [Installed Runtime State](#installed-runtime-state)
- [Runtime State Retention](#runtime-state-retention)
- [Documentation Responsibility](#documentation-responsibility)


## Top-Level Documents

| Path | Role |
| --- | --- |
| [README.md](../../README.md) | Concise public entry point and documentation hub |
| [README_ko.md](../../README_ko.md) | Korean paired public entry point |
| [architecture.md](../../architecture.md), [architecture_ko.md](../../architecture_ko.md) | Paired Core responsibility and data-flow map |
| [AGENTS.md](../../AGENTS.md) | Project loader for the current host contract |
| [Common governance](../../platforms/common-governance.md) | Shared contract source composed with host-specific rules |
| [CLAUDE.md](https://github.com/AidALL/ghost-alice/blob/main/CLAUDE.md) | Claude Code project rule entrypoint |
| [index.html](../../index.html) | GitHub Pages homepage |

## Installer And Platforms

| Path | Role |
| --- | --- |
| [install.sh](https://github.com/AidALL/ghost-alice/blob/main/install.sh) | macOS, Linux, WSL, and Git Bash installer entrypoint |
| [install.ps1](https://github.com/AidALL/ghost-alice/blob/main/install.ps1) | Windows installer entrypoint for PowerShell 7.6 or newer |
| [install.cmd](https://github.com/AidALL/ghost-alice/blob/main/install.cmd) | Windows CMD wrapper |
| [platforms/codex/](https://github.com/AidALL/ghost-alice/tree/main/platforms/codex) | Codex bootstrap source |
| [hooks/](https://github.com/AidALL/ghost-alice/tree/main/hooks) | Repository hook-related files |

For the full installer architecture, read [installer architecture](./installer-architecture.md).

## Shared Utilities

| Path | Role |
| --- | --- |
| [_shared/](https://github.com/AidALL/ghost-alice/tree/main/_shared) | Helpers shared by installers and skills |
| [_shared/secrets/](https://github.com/AidALL/ghost-alice/tree/main/_shared/secrets) | External credential lookup helpers |
| [_shared/mcp/](https://github.com/AidALL/ghost-alice/tree/main/_shared/mcp) | MCP installation helpers |
| [scripts/](https://github.com/AidALL/ghost-alice/tree/main/scripts) | Catalog, validation, installer compatibility, and public surface verification scripts |

## Core Governance And Runtime Gates

| Path | Role |
| --- | --- |
| [session-intent-analyzer/](https://github.com/AidALL/ghost-alice/tree/main/session-intent-analyzer) | Records compressed user-intent facts and decisions through the session intake procedure |
| [task-router/](https://github.com/AidALL/ghost-alice/tree/main/task-router) | Consumes exact session intent and selects current work and skills |
| [boundary-contract/](https://github.com/AidALL/ghost-alice/tree/main/boundary-contract) | Work boundary contract gate |
| [coding-convention/](https://github.com/AidALL/ghost-alice/tree/main/coding-convention) | Development workflow family |
| [skill-catalog/](https://github.com/AidALL/ghost-alice/tree/main/skill-catalog) | Generated gate metadata used by the runtime |
| [_shared/native_completion.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/native_completion.py) | Single Core assessment read by the Stop observer |

## Official Docs And Policies

| Path | Role |
| --- | --- |
| [official-docs/derived/](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived) | Public contributor compliance procedures; long-form philosophy lives in the Wiki |
| [docs/policies/](https://github.com/AidALL/ghost-alice/tree/main/docs/policies) | Runtime, platform, and evaluator policy documents |

## Installed Runtime State

These paths live outside the repository under the user's home directory.

| Path | Role |
| --- | --- |
| `~/.ghost-alice/install-state/` | Platform install-state manifests |
| `~/.ghost-alice/pending-merges/` | Protected queue for user-modified installed files |
| `~/.ghost-alice/uninstall-reports/` | Uninstall reports |
| `~/.ghost-alice/secrets.env` | Standard credential helper storage |
| `~/.agents/skills/` | Codex user skill copy install target |
| `~/.claude/skills/` | Claude Code skill install target |

The platform installation supplies common governance for every project. The Codex managed loader names `~/.codex/ghost-alice-governance.md`; Claude's managed global `CLAUDE.md` contains the common contract. Project instruction files supply project-specific constraints. The [installation guide](../getting-started/installation.md#common-governance-and-project-instructions) describes the source, installation and model handoffs. Runtime session state is authoritative in `.tmp/session-intent/ghost-state.sqlite3` at the receipt's repository root; JSON pointers and snapshots are compatibility/export material. Read state through `session_intent_ledger.py --read-state` with the receipt root, platform and session identity.

## Runtime State Retention

`_shared/state_retention.py` holds one table of the files and folders a Core installation creates under `~/.ghost-alice` and the host folders, with the rule that keeps each one bounded. The writer of a file applies the rule when it writes, so the code that creates a file also removes what the rule no longer keeps. Logs that several processes append to, such as `io-trace.jsonl` and `governance-events.jsonl`, append and apply their rule under one lock on the log, so no writer rotates on a size it read before another writer rotated. A removal that fails never stops the writer: it adds one line to `~/.ghost-alice/state-retention-failures.jsonl`, which keeps its last 500 lines, and `--doctor` reports it on the `state-retention` line. A writer that waits more than two seconds for a log's lock appends its line without applying the rule and records the wait in the same file. The installer records there a staging folder or an earlier installer's rollback folder that it cannot remove. A test installs into an empty home, runs every registered hook, forwards a gateway request and uninstalls; it fails when a created file or folder matches no entry of the table. Another test fails when an entry that removes files has no writer that applies it and names no change that adds one.

| Path | Rule | Applied by |
| --- | --- | --- |
| `~/.ghost-alice/session-logs/<host>/<session>/` | Session folders whose hook log is older than 14 days are removed in every host folder, oldest first, at most 50 per call | The first hook log row of a new session |
| `~/.ghost-alice/io-trace.jsonl` | Past 10,000 lines, the last 5,000 stay | Each io-trace row |
| `~/.ghost-alice/governance-events.jsonl` | Past 5 MB it becomes the one previous file `governance-events.jsonl.1` | Each governance event |
| `~/.ghost-alice/install/` | The last 30 install runs, each a log and its event trace | The installer, before it opens a new log |
| `~/.ghost-alice/install-state/<host>-events.jsonl` | Past 5 MB it becomes the one previous file | Each publish failure or retirement event |
| `~/.ghost-alice/rollbacks/` | The newest 20 finished publishes; an unfinished publish stays until it is restored | Each publish |
| `~/.ghost-alice/uninstall-reports/` | The newest 30 reports | The uninstall, before it writes a report |
| `~/.ghost-alice/context-gateway/usage.jsonl` | Past 20 MB it becomes the one previous file | Each gateway event |
| `~/.ghost-alice/context-gateway/service.log` | Past 5 MB it is copied to `service.log.1` and emptied, because launchd keeps it open for the service | The gateway, when it starts |
| `~/.ghost-alice/context-gateway/backups/` | The newest 5 registrations and the backup the install record names | Each committed gateway registration |
| `settings.json.bak*` and `hooks.json.bak*` in the host folders | The newest 3 | Each hook settings write |

A file far past its limit keeps only its newest part, up to the limit, as the previous file. The gateway records each request field as its item count, byte total and counts by type and role, records one usage value per usage event, and names in `response-end` how the response ended: `completed`, `client-closed` or `upstream-closed`. A client that closes its connection leaves that result instead of a traceback in `service.log`. Install records, host settings, installed skills and the runtime are overwritten in place. The program never removes the uninstall backups under `~/.ghost-alice/uninstall-backup/<host>/support-state/`, pending merge copies or retirement backups, because they can hold the user's changes. The Core ledger database `.tmp/session-intent/ghost-state.sqlite3` at the receipt's repository root is outside this table and keeps its rows without a time limit.

## Documentation Responsibility

- README holds the public description and quick start only.
- Detailed installation and update guidance lives in [installation guide](../getting-started/installation.md).
- Contributor-facing installer flow and safety model live in [installer architecture](./installer-architecture.md).
- Policy and runtime matrices live in [docs/policies](https://github.com/AidALL/ghost-alice/tree/main/docs/policies).
- Long-form philosophy and closed-loop reasoning live in the [Wiki](https://github.com/AidALL/ghost-alice/wiki); [official-docs/derived](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived) holds public contributor procedures.
