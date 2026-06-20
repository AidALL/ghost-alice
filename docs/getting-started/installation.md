# Installation And Update Guide

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/getting-started/installation.md)

This guide contains the detailed Ghost-ALICE OS installation, update, and recovery workflow. The root README keeps only the quick path.
## Contents

- [Quick Install](#quick-install)
- [Install One Platform](#install-one-platform)
- [Agent Visibility Profile](#agent-visibility-profile)
- [Python Contract](#python-contract)
- [Node.js Contract](#nodejs-contract)
- [Check Status](#check-status)
- [Update](#update)
- [Platform Update Behavior](#platform-update-behavior)
- [Installed Surfaces](#installed-surfaces)
- [merge-companion](#merge-companion)
- [Common Commands](#common-commands)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)


## Quick Install

```bash
git clone https://github.com/AidALL/ghost-alice.git ~/ghost-alice
cd ~/ghost-alice
bash install.sh
```

Windows PowerShell:

```powershell
.\install.cmd
```

Windows CMD:

```cmd
install.cmd
```

`install.cmd` is a wrapper for environments where PowerShell execution policy blocks direct script execution or profile loading. It calls `install.ps1` internally with `-NoProfile -ExecutionPolicy Bypass` and does not change the user or machine execution policy.

## Install Official Addons

Official addons use short aliases and install to detected Claude Code/Codex targets by default:

```bash
cd ~/ghost-alice
bash install.sh --addon autopilot
```

Run this from the Ghost-ALICE core checkout. Normal users do not clone the autopilot addon repository or run an installer inside that repository; the core installer fetches the official addon package.

To install the official addon for one platform only, add `--platform`:

```bash
bash install.sh --platform codex --addon autopilot
```

Windows PowerShell/CMD use the same official alias from the core checkout:

```powershell
.\install.cmd --addon autopilot
```

Custom, tenant, or local development addons still use `--addon-source PATH|URL`:

```bash
bash install.sh --addon-source /path/to/addon-repo
```

## Install One Platform

| Target | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| Claude Code | `bash install.sh --platform claude` | `.\install.cmd -Platform claude` | `install.cmd -Platform claude` |
| Codex | `bash install.sh --platform codex` | `.\install.cmd -Platform codex` | `install.cmd -Platform codex` |

To choose interactively, use `bash install.sh --prompt-platform`, `.\install.cmd -PromptPlatform`, or `install.cmd -PromptPlatform`.

## Agent Visibility Profile

The default profile is `dynamic`. The profile controls how much governance
surface is shown to the user; it does not disable hooks, strict-grade logs, or
Work-Impact Projection.

| Profile | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| strict | `bash install.sh --visibility strict` | `.\install.cmd -Visibility strict` | `install.cmd -Visibility strict` |
| dynamic | `bash install.sh --visibility dynamic` | `.\install.cmd -Visibility dynamic` | `install.cmd -Visibility dynamic` |
| minimal | `bash install.sh --visibility minimal` | `.\install.cmd -Visibility minimal` | `install.cmd -Visibility minimal` |

`--agent-visibility` and `-AgentVisibility` remain accepted compatibility
aliases. Prefer `--visibility` and `-Visibility` in new docs and commands.

## Slash Commands by Platform

Claude Code treats slash commands as a first-class feature: `/visibility` and
skill `/name` invocation are officially supported, with custom commands in
`.claude/commands/` and `.claude/skills/<name>/SKILL.md` as the current form.
See https://code.claude.com/docs/en/agent-sdk/slash-commands.

Codex also documents slash commands (built-ins such as `/plan`, plus custom
prompts under `~/.codex/prompts/`), but the custom-command surface is
version-dependent, custom prompts are deprecated in favor of skills, and Codex
does not auto-invoke slash commands from instructions the way Claude does. On
Codex, type `/visibility` manually in a trusted session, and use
`python3 _shared/agent_visibility_cli.py set <profile>` as the reliable
cross-platform path. See https://developers.openai.com/codex/cli/slash-commands
and https://developers.openai.com/codex/custom-prompts.

## Python Contract

The installer requires Python 3.11 or newer. If Python 3.11+ is missing, it attempts automatic preparation where possible.

- macOS: if Homebrew is available, `brew install python3`
- Linux / WSL: available package managers such as `apt-get`, `dnf`, `yum`, or `pacman`
- Windows: `winget`, `choco`, then `scoop`

If Python 3.11+ is still unavailable, installation stops and prints manual recovery guidance.

## Node.js Contract

Claude Code and Codex hook-enabled installs require Node.js on `PATH` because the `tool-checkpoint` PreToolUse gate runs `ghost-alice-hook.mjs`. The installer blocks hook installation when the target platform is present but `node` is unavailable; this prevents installing a Node-backed gate that cannot execute at runtime.

## Check Status

Run doctor first when installation state looks suspicious. Doctor is a read-only strict diagnostic.

```bash
bash install.sh --doctor
bash install.sh --status
```

PowerShell:

```powershell
.\install.cmd -Doctor
.\install.cmd -Status
```

CMD:

```cmd
install.cmd -Doctor
install.cmd -Status
```

The healthy target is `overall: ok`.

## Update

First update the local clone through the installer. This preserves source-local edits in `git stash` before fast-forwarding the checkout.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

PowerShell:

```powershell
.\install.cmd -UpdateSource
```

If the checkout is too old to receive that option because raw `git pull` is already blocked by local changes, use the bootstrap updater instead. This command fetches the current updater through the already configured Git remote, saves local changes in `git stash`, fast-forwards `~/ghost-alice`, and then runs the updated installer.

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

PowerShell:

```powershell
.\install.cmd
.\install.cmd -Doctor
.\install.cmd -Status
```

## Platform Update Behavior

| Platform | OS | Install mode | Install path | Skill body updates auto-reflect |
| --- | --- | --- | --- | --- |
| Claude Code | macOS / Linux / WSL | symlink | `~/.claude/skills/` | yes |
| Claude Code | Windows | junction | `~/.claude/skills/` | yes |
| Codex | macOS / Linux / WSL | copy | `~/.agents/skills/` | no |
| Codex | Windows | copy | `~/.agents/skills/` | no |
| All platforms | Git Bash on Windows | copy fallback | varies | no |

Auto-reflection applies to skill bodies such as `SKILL.md`, `references/`, and `scripts/`. Hooks, bootstrap files, permission policy, and `_shared/` are installer-managed runtime surfaces, so rerun the installer when they change.

## Installed Surfaces

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

## merge-companion

When an update detects user-modified installed files, the installer isolates those candidates in a pending merge queue.

- manifest: `~/.ghost-alice/pending-merges/<platform>/manifest.json`
- backup: `~/.ghost-alice/pending-merges/<platform>/`
- install state: `~/.ghost-alice/install-state/<platform>.json`

On the next Claude/Codex session, if any pending entries exist, `merge-companion` asks whether each should be merged, discarded, or deferred.

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

## Uninstall

Uninstall uses the installer-owned install-state manifest.

```bash
bash install.sh --uninstall
```

See [uninstall cleanup](./uninstall.md) for the full cleanup contract.

## Troubleshooting

When an update is blocked during `git pull`, a merge conflict, or a PowerShell installer rerun, start with [troubleshooting](./troubleshooting.md).

The same recovery playbook is mirrored in the GitHub Wiki `install-troubleshooting` page for people who cannot pull the repo yet.
