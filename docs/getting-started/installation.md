# Installation And Update Guide

Language: English | [Korean](../ko/getting-started/installation.md)

This guide is the command reference for installing, updating, checking, and repairing Ghost-ALICE OS. Start with the command path you need, then use the runtime reference only when you need platform details.

## Contents

- [Quick Install](#quick-install)
- [Install Official Addons](#install-official-addons)
- [Official Addon List](#official-addon-list)
- [Install One Official Addon To One Platform](#install-one-official-addon-to-one-platform)
- [Install Custom Addons](#install-custom-addons)
- [Install One Platform](#install-one-platform)
- [Check Status](#check-status)
- [Update](#update)
- [Common Commands](#common-commands)
- [Runtime And Platform Reference](#runtime-and-platform-reference)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)

## Quick Install

```bash
git clone https://github.com/AidALL/ghost-alice.git ~/ghost-alice
cd ~/ghost-alice
bash install.sh
```

Windows PowerShell / CMD:

```powershell
.\install.cmd
```

```cmd
install.cmd
```

`install.cmd` is a wrapper for environments where PowerShell execution policy blocks direct script execution or profile loading. It calls `install.ps1` internally with `-NoProfile -ExecutionPolicy Bypass` and does not change the user or machine execution policy.

## Install Official Addons

Official addons use short aliases and install from the Ghost-ALICE core checkout.

```bash
bash install.sh --addon autopilot
```

```powershell
.\install.cmd --addon autopilot
```

Addon-specific behavior, state files, pause/resume controls, and removal details live in each addon repository. The core checkout owns the common install command.

## Official Addon List

| Addon | Purpose | Basic install | Details |
| --- | --- | --- | --- |
| autopilot | Continue explicitly approved autonomous runs one work item at a time | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

## Install One Official Addon To One Platform

```bash
bash install.sh --platform codex --addon autopilot
```

```powershell
.\install.cmd -Platform codex --addon autopilot
```

## Install Custom Addons

Custom, tenant, or local development addons use `--addon-source PATH|URL`.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

```powershell
.\install.cmd -AddonSource C:\path\addon-repo
```

For git URL addon sources, `-AddonTag` selects the branch or tag to clone into the local addon source cache.

## Install One Platform

| Target | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| Claude Code | `bash install.sh --platform claude` | `.\install.cmd -Platform claude` | `install.cmd -Platform claude` |
| Codex | `bash install.sh --platform codex` | `.\install.cmd -Platform codex` | `install.cmd -Platform codex` |

To choose interactively, use `bash install.sh --prompt-platform`, `.\install.cmd -PromptPlatform`, or `install.cmd -PromptPlatform`.

## Check Status

Doctor is a read-only strict diagnostic. Use it before changing a suspicious install.

```bash
bash install.sh --doctor
bash install.sh --status
```

```powershell
.\install.cmd -Doctor
.\install.cmd -Status
```

The healthy target is `overall: ok`.

## Update

Update the local clone through the installer so source-local edits are stashed before a fast-forward.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

```powershell
.\install.cmd -UpdateSource
```

If the checkout is too old to receive that option because raw `git pull` is already blocked by local changes, use the bootstrap updater.

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

If source update stops with conflicts, divergent branches, or non-fast-forward state, do not rerun the installer repeatedly. Follow [troubleshooting](./troubleshooting.md) first.

Then rerun the installer.

```bash
bash install.sh
bash install.sh --doctor
bash install.sh --status
```

## Common Commands

| Purpose | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| List skills | `bash install.sh --list` | `.\install.cmd -List` | `install.cmd -List` |
| Show install state | `bash install.sh --status` | `.\install.cmd -Status` | `install.cmd -Status` |
| Run protected diagnostic | `bash install.sh --doctor` | `.\install.cmd -Doctor` | `install.cmd -Doctor` |
| Safe source update | `bash install.sh --update-source` | `.\install.cmd -UpdateSource` | `install.cmd -UpdateSource` |
| Install official autopilot addon | `bash install.sh --addon autopilot` | `.\install.cmd --addon autopilot` | `install.cmd --addon autopilot` |
| Install custom addon source | `bash install.sh --addon-source /path/to/addon-repo` | `.\install.cmd -AddonSource C:\path\addon-repo` | `install.cmd -AddonSource C:\path\addon-repo` |
| Selective core install | `bash install.sh task-router verification-before-completion` | `.\install.cmd -Skills task-router,verification-before-completion` | `install.cmd -Skills task-router,verification-before-completion` |
| Full uninstall | `bash install.sh --uninstall` | `.\install.cmd -Uninstall` | `install.cmd -Uninstall` |
| Selective uninstall | `bash install.sh --platform codex --uninstall task-router` | `.\install.cmd -Platform codex -Uninstall -Skills task-router` | `install.cmd -Platform codex -Uninstall -Skills task-router` |
| Clean false pending entries | `bash install.sh --platform claude --cleanup-pending` | `.\install.cmd -Platform claude -CleanupPending` | `install.cmd -Platform claude -CleanupPending` |

## Runtime And Platform Reference

### Agent Visibility Profile

The default profile is `dynamic`. The profile controls how much governance surface is shown to the user; it does not disable hooks, strict-grade logs, or Work-Impact Projection.

| Profile | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| strict | `bash install.sh --visibility strict` | `.\install.cmd -Visibility strict` | `install.cmd -Visibility strict` |
| dynamic | `bash install.sh --visibility dynamic` | `.\install.cmd -Visibility dynamic` | `install.cmd -Visibility dynamic` |
| minimal | `bash install.sh --visibility minimal` | `.\install.cmd -Visibility minimal` | `install.cmd -Visibility minimal` |

`--agent-visibility` and `-AgentVisibility` remain accepted compatibility aliases. Prefer `--visibility` and `-Visibility` in new docs and commands.

### Slash Commands By Platform

Claude Code treats slash commands as a first-class feature. Codex supports built-in slash commands and a custom prompt path, but stable Ghost-ALICE profile changes should use `_shared/agent_visibility_cli.py` when a trusted runtime command is not available.

### Python Contract

The installer requires Python 3.11 or newer. If Python 3.11+ is missing, it attempts automatic preparation where possible.

- macOS: if Homebrew is available, `brew install python3`
- Linux / WSL: available package managers such as `apt-get`, `dnf`, `yum`, or `pacman`
- Windows: `winget`, `choco`, then `scoop`

If Python 3.11+ is still unavailable, installation stops and prints manual recovery guidance.

### Node.js Contract

Claude Code and Codex hook-enabled installs require Node.js on `PATH` because the `tool-checkpoint` PreToolUse gate runs `ghost-alice-hook.mjs`. The installer blocks hook installation when the target platform is present but `node` is unavailable.

### Platform Update Behavior

| Platform | OS | Install mode | Install path | Skill body updates auto-reflect |
| --- | --- | --- | --- | --- |
| Claude Code | macOS / Linux / WSL | symlink | `~/.claude/skills/` | yes |
| Claude Code | Windows | junction | `~/.claude/skills/` | yes |
| Codex | macOS / Linux / WSL | copy | `~/.agents/skills/` | no |
| Codex | Windows | copy | `~/.agents/skills/` | no |
| All platforms | Git Bash on Windows | copy fallback | varies | no |

Auto-reflection applies to skill bodies such as `SKILL.md`, `references/`, and `scripts/`. Hooks, bootstrap files, permission policy, and `_shared/` are installer-managed runtime surfaces, so rerun the installer when they change.

### Installed Surfaces

A full install deploys:

- core skills from `skill-catalog/skills.json`
- coding-convention workflow skills
- optional addon skills through official `--addon` aliases or custom `--addon-source`
- `_shared/` utilities
- platform hook settings
- Node-backed hook dispatcher assets under `~/.ghost-alice/hooks/`
- Claude Code permission allowlist
- Codex `~/.codex/AGENTS.md` bootstrap
- support state for install-state, pending merges, install rollbacks, and uninstall reports

### merge-companion

When an update detects user-modified installed files, the installer isolates those candidates in a pending merge queue.

- manifest: `~/.ghost-alice/pending-merges/<platform>/manifest.json`
- backup: `~/.ghost-alice/pending-merges/<platform>/`
- install state: `~/.ghost-alice/install-state/<platform>.json`

On the next Claude/Codex session, if pending entries exist, `merge-companion` asks whether each should be merged, discarded, or deferred.

## Uninstall

Uninstall uses the installer-owned install-state manifest.

```bash
bash install.sh --uninstall
```

See [uninstall cleanup](./uninstall.md) for the full cleanup contract.

## Troubleshooting

When an update is blocked during `git pull`, a merge conflict, or a PowerShell installer rerun, start with [troubleshooting](./troubleshooting.md).

The same recovery playbook is mirrored in the GitHub Wiki `install-troubleshooting` page for people who cannot pull the repo yet.
