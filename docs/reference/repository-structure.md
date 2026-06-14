# Repository Structure

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/repository-structure.md)

This document is the repository map split out of the root README. The README stays a concise public entry point; this page carries the detailed structure.
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
| [AGENTS.md](../../AGENTS.md) | Project rules and runtime gate SSOT |
| [CLAUDE.md](../../CLAUDE.md) | Claude Code project rule entrypoint |
| [docs/index.html](../index.html) | GitHub Pages homepage |

## Installer And Platforms

| Path | Role |
| --- | --- |
| [install.sh](../../install.sh) | macOS, Linux, WSL, and Git Bash installer entrypoint |
| [install.ps1](../../install.ps1) | Windows PowerShell installer entrypoint |
| [install.cmd](../../install.cmd) | Windows CMD wrapper |
| [platforms/codex/](../../platforms/codex/) | Codex bootstrap source |
| [hooks/](../../hooks/) | Repository hook-related files |

For the full installer architecture, read [installer architecture](./installer-architecture.md).

## Shared Utilities

| Path | Role |
| --- | --- |
| [_shared/](../../_shared/) | Helpers shared by installers and skills |
| [_shared/secrets/](../../_shared/secrets/) | External credential lookup helpers |
| [_shared/mcp/](../../_shared/mcp/) | MCP installation helpers |
| [scripts/](../../scripts/) | Catalog, validation, installer compatibility, and public surface verification scripts |

## Core Governance And Runtime Gates

| Path | Role |
| --- | --- |
| [session-intent-analyzer/](../../session-intent-analyzer/) | Canonical path for the session-intent-analyzer intent state producer |
| [task-router/](../../task-router/) | Internal core gate that derives capability routing |
| [boundary-contract/](../../boundary-contract/) | Work boundary contract gate |
| [coding-convention/](../../coding-convention/) | Development workflow family |
| [skill-catalog/](../../skill-catalog/) | Generated gate metadata used by the runtime |

## Official Docs And Policies

| Path | Role |
| --- | --- |
| [official-docs/derived/](../../official-docs/derived/) | Ghost-ALICE analysis, philosophy, compliance, and closed-loop SSOT |
| [docs/policies/](../policies/) | Runtime, platform, and evaluator policy documents |
| [docs/plans/](../plans/) | Public roadmap policy and planning boundaries |

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

## Documentation Responsibility

- README holds the public description and quick start only.
- Detailed installation and update guidance lives in [installation guide](../getting-started/installation.md).
- Contributor-facing installer flow and safety model live in [installer architecture](./installer-architecture.md).
- Policy and runtime matrices live in [docs/policies](../policies/).
- Long-form philosophy and closed-loop reasoning live in [official-docs/derived](../../official-docs/derived/).
