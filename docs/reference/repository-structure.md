# Repository Structure

[🇺🇸 English](./repository-structure.md) | [🇰🇷 한국어](../ko/reference/repository-structure.md)

This document is the repository map.
## Contents

- [Top-Level Documents](#top-level-documents)
- [Installer And Platforms](#installer-and-platforms)
- [Shared Utilities](#shared-utilities)
- [Core Governance And Runtime Gates](#core-governance-and-runtime-gates)
- [Official Docs And Policies](#official-docs-and-policies)
- [Installed Runtime State](#installed-runtime-state)
- [Runtime State Retention](#runtime-state-retention)


## Top-Level Documents

| Path | Role |
| --- | --- |
| [README.md](../../README.md) | Concise public entry point |
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
| [session-intent-analyzer/](https://github.com/AidALL/ghost-alice/tree/main/session-intent-analyzer) | Guides the response model in recording its `interpreted-as` record and the objective, scope, constraints, decisions, open questions and acceptance criteria in the intent ledger; each item's source label names its origin, and an item without a source label has no recorded origin |
| [task-router/](https://github.com/AidALL/ghost-alice/tree/main/task-router) | Guides the response model in reading its exact `interpreted-as` record and selecting current work and skills |
| [boundary-contract/](https://github.com/AidALL/ghost-alice/tree/main/boundary-contract) | Guides the response model in writing its boundary contract: the objective that the human user's instructions set; the allowed targets and actions, phase and resource limits that the response model declares within the work that those instructions authorize; the prohibited paths, layers and actions, whether the human user prohibited them, the project instructions prohibit them or the response model excluded them as its self-declared narrower limit; acceptance criteria and stop conditions |
| [coding-convention/](https://github.com/AidALL/ghost-alice/tree/main/coding-convention) | Development workflow family |
| [skill-catalog/](https://github.com/AidALL/ghost-alice/tree/main/skill-catalog) | The skill catalog `skills.json` generated from each `SKILL.md` frontmatter and its schema `schema.json`, and the addon manifest schema `addon-schema.json` |
| [_shared/native_completion.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/native_completion.py) | Single Core assessment the Stop script calls |
| [_shared/stop_notice.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/stop_notice.py) | Stop notice message: built by the Stop script and recorded in `stop-notice.json` in the session folder next to the hook log; the hook runner passes the file's path to Stop hooks in `GHOST_ALICE_STOP_NOTICE_FILE` |

## Official Docs And Policies

| Path | Role |
| --- | --- |
| [official-docs/derived/](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived) | Public contributor compliance procedures; long-form philosophy lives in the Wiki |
| [docs/policies/](https://github.com/AidALL/ghost-alice/tree/main/docs/policies) | English runtime, platform, and evaluator policy documents |
| [docs/ko/policies/](https://github.com/AidALL/ghost-alice/tree/main/docs/ko/policies) | Korean editions of the policy documents |

## Installed Runtime State

These paths live outside the repository under the user's home directory.

| Path | Role |
| --- | --- |
| `~/.ghost-alice/install-state/` | Platform install-state manifests |
| `~/.ghost-alice/pending-merges/` | The installer's protected backups of external changes: changes it finds in installed targets after install that its records do not hold; the installer does not record who made the changes |
| `~/.ghost-alice/uninstall-reports/` | Uninstall reports |
| `~/.ghost-alice/secrets.env` | Standard credential helper storage |
| `~/.agents/skills/` | Codex user skill copy install target |
| `~/.claude/skills/` | Claude Code skill install target |

The platform installation supplies common governance for every project. The Codex managed loader names `~/.codex/ghost-alice-governance.md`; Claude's managed global `CLAUDE.md` contains the common contract. Project instruction files supply project-specific constraints. The [installation guide](../getting-started/installation.md#common-governance-and-project-instructions) describes the source, installation and model handoffs.

## Runtime State Retention

`_shared/state_retention.py` names the files and folders a Core installation creates under `~/.ghost-alice` and the host folders, and each item's retention rule. Writers apply bounded log rules when they write. Concurrent appenders use one log lock for append and rotation. Failed best-effort log maintenance records a line in `~/.ghost-alice/state-retention-failures.jsonl` and lets the writer continue; that file keeps its last 500 lines and `--doctor` reports it. A writer that waits more than two seconds appends without applying retention and records the wait. An incomplete log append records its failure when possible; Windows and file systems that refuse `flock` use the existing unlocked behavior. Staging-folder cleanup failures use the same diagnostic file. Required retirement and obsolete-copy cleanup follow their [owning procedure](installer-architecture.md#retirement-and-final-cleanup), which reports incomplete work to the caller. The retention table's `retired-assets` entry uses the naming-only method `owner-completed`; `state_retention.apply()` does not delete those copies. Tests check that created paths have a table entry and that automatic deletion rules have a writer that applies them.

| Path | Rule | Applied by |
| --- | --- | --- |
| `~/.ghost-alice/session-logs/<host>/<session>/` | Session folders whose hook log is older than 14 days are removed in every host folder, oldest first, at most 50 per call | The first hook log row of a new session |
| `~/.ghost-alice/io-trace.jsonl` | Past 10,000 lines, the last 5,000 stay | Each io-trace row |
| `~/.ghost-alice/install/` | The last 30 install runs, each a log and its event trace | The installer, before it opens a new log |
| `~/.ghost-alice/install-state/<host>-events.jsonl` | Past 5 MB it becomes the one previous file | Each publish failure or retirement event |
| `~/.ghost-alice/rollbacks/` | `owner-completed`: remove unchanged managed originals without current dependencies; preserve unfinished recovery and external changes | [Completed-publish cleanup](installer-architecture.md#successful-code-versions) after a successful full or partial Core installation |
| `~/.ghost-alice/versions/` | Three distinct successful code versions, including the current version; identify any active or modified versions retained beyond that limit | [Successful-source owner](installer-architecture.md#successful-code-versions) after completed-publish cleanup |
| `~/.ghost-alice/deprecated-skill-backups/`, `~/.claude/retired-command-backups/` | `owner-completed`: the owner deletes copies whose completion evidence resolves recovery and review; incomplete, modified and unidentified copies stay | [Final cleanup owner](installer-architecture.md#retirement-and-final-cleanup) |
| `~/.ghost-alice/uninstall-reports/` | The newest 30 reports | The uninstall, before it writes a report |
| `~/.ghost-alice/context-gateway/usage.jsonl` | Past 20 MB it becomes the one previous file | Each gateway event |
| `~/.ghost-alice/context-gateway/service.log` | Past 5 MB it is copied to `service.log.1` and emptied, because launchd keeps it open for the service | The gateway, when it starts |
| `~/.ghost-alice/context-gateway/backups/` | The newest 5 registrations and the backup the install record names | Each committed gateway registration |
| `settings.json.bak*` and `hooks.json.bak*` in the host folders | The newest 3 | Each hook settings write |

A file far past its limit keeps only its newest part, up to the limit, as the previous file. The gateway records each request field as its item count, byte total and counts by type and role, records one usage value per usage event, and names in `response-end` how the response ended: `completed`, `client-closed` or `upstream-closed`. A client that closes its connection leaves that result instead of a traceback in `service.log`. Install records, host settings, installed skills and the runtime are overwritten in place. The retention program preserves uninstall backups under `~/.ghost-alice/uninstall-backup/<host>/support-state/` and pending merge copies that can hold external changes. Retirement copies follow the owner-completed rule above. The Core ledger database `.tmp/session-intent/ghost-state.sqlite3` at the receipt's repository root is outside this table and keeps its rows without a time limit.
