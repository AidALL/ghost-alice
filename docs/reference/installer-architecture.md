# Installer Architecture

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/installer-architecture.md)

This document is the contributor bridge between the quick installation docs and the installer compatibility contracts. It does not make the whole installer trivial. It gives contributors the architecture map they need before tracing a change through the installer, hook payloads, install state, and tests.

For quick user instructions, read [installation](../getting-started/installation.md). For compatibility requirements and test ownership, read [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md).
## Contents

- [Reading Path](#reading-path)
- [Architecture Summary](#architecture-summary)
- [Install Flow](#install-flow)
- [Platform Surfaces](#platform-surfaces)
- [State And Safety Model](#state-and-safety-model)
- [Contributor Entry Points](#contributor-entry-points)
- [What Not To Infer](#what-not-to-infer)
- [Full Understanding](#full-understanding)


## Reading Path

1. Use this page to understand the moving parts and the call direction.
2. Use [repository structure](./repository-structure.md) to find the files.
3. Use [installation](../getting-started/installation.md) and [uninstall](../getting-started/uninstall.md) to understand user-facing behavior.
4. Use [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md), [tool output semantics](../policies/tool-output-semantics.md), and [live smoke regression](../policies/live-smoke-regression.md) to understand the contract and verification burden.

## Architecture Summary

The installer has three layers.

| Layer | Main files | Responsibility |
| --- | --- | --- |
| Shell entrypoints | `install.sh`, `install.ps1`, `install.cmd` | Parse platform-specific flags, normalize encoding/runtime setup, and enter the Python-backed installer path |
| Installer orchestration | `_shared/install_hooks.py`, `installer_lib/`, `_shared/install_transaction.py`, `_shared/install_state_writer.py` | Resolve platforms and targets, install skills and hooks, record install state, protect local edits, and report status |
| Runtime surfaces | `~/.claude/`, `~/.codex/`, `~/.agents/skills/`, `~/.ghost-alice/` | Hold installed skills, platform hook config, Codex bootstrap instructions, install-state manifests, hook feature rollback metadata, pending merges, and uninstall reports |

The important mental model is not "copy files, then done." The installer is a stateful governance synchronizer. It updates installed assets, verifies hook contracts, preserves user-modified installed files, and writes enough state for status, doctor, update, and uninstall paths to reason about what happened.

## Install Flow

```text
shell entrypoint
-> runtime and source-health preflight
-> platform and skill target resolution
-> skill and shared-helper copy install
-> platform hook and bootstrap sync
-> install-state and event recording
-> status/report output
```

`install.cmd` is only a Windows wrapper. The substantive Windows path is `install.ps1`. On Unix-like environments, `install.sh` owns the shell entry path and re-enters Bash when needed.

## Platform Surfaces

| Platform | Installed surfaces |
| --- | --- |
| Claude Code | `~/.claude/skills/`, `~/.claude/settings.json`, Claude command wrappers |
| Codex | `~/.agents/skills/`, `~/.codex/AGENTS.md`, `~/.codex/hooks.json`, `~/.codex/config.toml` |
| Shared state | `~/.ghost-alice/install-state/`, `~/.ghost-alice/pending-merges/`, `~/.ghost-alice/uninstall-reports/`, `~/.ghost-alice/install/` |

Claude Code can expose native skill invocation and hook permissions. Codex does not expose the same skill surface, so the installer also installs a Codex bootstrap and hook config that make required gates auditable through `SKILL.md` read records and hook payloads.

## State And Safety Model

The installer records what it changes instead of guessing during cleanup.

| State path | Why it exists |
| --- | --- |
| `~/.ghost-alice/install-state/<platform>.json` | Records installer-owned targets and environment changes for status and uninstall |
| `~/.ghost-alice/pending-merges/<platform>/` | Preserves user-modified installed files that cannot be overwritten silently |
| `~/.ghost-alice/install/` | Keeps install reports and event traces for audit and recovery |

This is why update and uninstall are part of the installer architecture, not separate chores. A change that affects copied assets, hooks, or runtime config usually also affects status, doctor, pending merge behavior, and uninstall rollback.

## Contributor Entry Points

| Change type | Start here | Then verify with |
| --- | --- | --- |
| Shell flag or platform bootstrap | `install.sh`, `install.ps1`, `install.cmd` | shell parser tests, runtime detection tests, compatibility matrix groups |
| Hook payload or hook status wording | `_shared/install_hooks.py` | `_shared.test_install_hooks`, `scripts/check_skill_gate_contract.py` |
| Install-state or uninstall behavior | `_shared/install_state_writer.py`, `_shared/uninstall_cleanup.py`, `installer_lib/` | install-state schema tests and uninstall tests |
| Public docs or command surface | `README.md`, `docs/`, `docs/index.html` | `scripts/validate_public_surfaces.py` and public-surface contract tests |
| Platform compatibility rule | `docs/policies/installer-platform-compatibility-matrix.md` | `scripts/run_installer_compat_tests.py --list` and the focused group named by the policy |

When changing one part, look for the paired contract. The test owner named in the compatibility matrix is the fastest way to find the regression surface.

## What Not To Infer

- Hook files existing on disk does not prove hook payloads are current.
- A copied skill directory does not prove the platform can invoke or audit that skill.
- A passing install smoke does not prove status, doctor, update, pending merge, and uninstall paths are aligned.
- A user-facing install command does not explain the architecture; it is only the entrypoint.

## Full Understanding

A contributor can usually get oriented in a few minutes, but full installer understanding requires tracing one change through the entrypoint, orchestration layer, runtime surface, state file, and test owner. This page is the first map for that trace. It is not a substitute for reading the relevant implementation and compatibility contract.
