# Repository Structure

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/repository-structure.md)

This document is the repository map. The README stays a concise public entry point; this page carries the detailed structure.
## Contents

- [Top-Level Documents](#top-level-documents)
- [Installer And Platforms](#installer-and-platforms)
- [Shared Utilities](#shared-utilities)
- [Core Governance And Runtime Gates](#core-governance-and-runtime-gates)
- [Official Docs And Policies](#official-docs-and-policies)
- [Installed Runtime State](#installed-runtime-state)
- [Documentation Responsibility](#documentation-responsibility)


## Top-Level Documents

| Path | Role |
| --- | --- |
| [README.md](../../README.md) | Concise public entry point and documentation hub |
| [README_ko.md](../../README_ko.md) | Korean paired public entry point |
| [architecture.md](../../architecture.md), [architecture_ko.md](../../architecture_ko.md) | Paired responsibility and data-flow map |
| [AGENTS.md](https://github.com/AidALL/ghost-alice/blob/main/AGENTS.md) | Canonical common governance authored for the platform distributions |
| [CLAUDE.md](https://github.com/AidALL/ghost-alice/blob/main/CLAUDE.md) | Claude Code project rule entrypoint |
| [docs/index.html](../index.html) | GitHub Pages homepage |

## Installer And Platforms

| Path | Role |
| --- | --- |
| [install.sh](https://github.com/AidALL/ghost-alice/blob/main/install.sh) | macOS, Linux, WSL, and Git Bash installer entrypoint |
| [install.ps1](https://github.com/AidALL/ghost-alice/blob/main/install.ps1) | Windows PowerShell installer entrypoint |
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
| [_shared/native_completion.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/native_completion.py) | Single Core assessment consumed by the completion guard and Ghost-ALICE Autopilot |
| [_shared/original_claim_evidence.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/original_claim_evidence.py) | Authenticates retained claim bindings against current criteria and dependencies |

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

## Documentation Responsibility

- README holds the public description and quick start only.
- Detailed installation and update guidance lives in [installation guide](../getting-started/installation.md).
- Contributor-facing installer flow and safety model live in [installer architecture](./installer-architecture.md).
- Policy and runtime matrices live in [docs/policies](https://github.com/AidALL/ghost-alice/tree/main/docs/policies).
- Long-form philosophy and closed-loop reasoning live in the [Wiki](https://github.com/AidALL/ghost-alice/wiki); [official-docs/derived](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived) holds public contributor procedures.
