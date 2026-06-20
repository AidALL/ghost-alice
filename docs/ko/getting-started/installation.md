# Installation And Update Guide

언어: [English](../../getting-started/installation.md) | Korean

이 문서는 Ghost-ALICE OS 설치, 업데이트, 상태 점검, 복구 command reference다. 먼저 필요한 command path를 고르고, platform detail이 필요할 때만 runtime reference를 본다.

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

`install.cmd`는 PowerShell execution policy 또는 profile loading이 direct script execution을 막는 환경을 위한 wrapper다. 내부에서 `-NoProfile -ExecutionPolicy Bypass`로 `install.ps1`을 호출하고 사용자 또는 머신 execution policy를 변경하지 않는다.

## Install Official Addons

Official addons는 short alias를 사용하며 Ghost-ALICE core checkout에서 설치한다.

```bash
bash install.sh --addon autopilot
```

```powershell
.\install.cmd --addon autopilot
```

Addon-specific behavior, state files, pause/resume controls, removal details는 각 addon repository에 둔다. Core checkout은 common install command를 소유한다.

## Official Addon List

| Addon | Purpose | Basic install | Details |
| --- | --- | --- | --- |
| autopilot | explicitly approved autonomous run을 work item 단위로 계속 진행한다 | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

## Install One Official Addon To One Platform

```bash
bash install.sh --platform codex --addon autopilot
```

```powershell
.\install.cmd -Platform codex --addon autopilot
```

## Install Custom Addons

Custom, tenant, local development addon은 `--addon-source PATH|URL`을 사용한다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

```powershell
.\install.cmd -AddonSource C:\path\addon-repo
```

git URL addon source에서는 `-AddonTag`가 local addon source cache로 clone할 branch 또는 tag를 선택한다.

## Install One Platform

| Target | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| Claude Code | `bash install.sh --platform claude` | `.\install.cmd -Platform claude` | `install.cmd -Platform claude` |
| Codex | `bash install.sh --platform codex` | `.\install.cmd -Platform codex` | `install.cmd -Platform codex` |

Interactively 선택하려면 `bash install.sh --prompt-platform`, `.\install.cmd -PromptPlatform`, `install.cmd -PromptPlatform`를 사용한다.

## Check Status

Doctor는 read-only strict diagnostic이다. 설치 상태가 의심스러우면 변경 전에 doctor를 먼저 실행한다.

```bash
bash install.sh --doctor
bash install.sh --status
```

```powershell
.\install.cmd -Doctor
.\install.cmd -Status
```

정상 목표는 `overall: ok`다.

## Update

Installer를 통해 local clone을 업데이트한다. 이 경로는 fast-forward 전에 source-local edit을 stash한다.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

```powershell
.\install.cmd -UpdateSource
```

checkout이 너무 오래되어 raw `git pull`이 local changes로 막힌 경우 bootstrap updater를 사용한다.

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

source update가 conflicts, divergent branches, non-fast-forward state에서 멈추면 installer를 반복 실행하지 않는다. 먼저 [troubleshooting](./troubleshooting.md)을 따른다.

그 다음 installer를 다시 실행한다.

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

default profile은 `dynamic`이다. profile은 user-facing governance surface 양만 제어한다. hooks, strict-grade logs, Work-Impact Projection을 disable하지 않는다.

| Profile | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| strict | `bash install.sh --visibility strict` | `.\install.cmd -Visibility strict` | `install.cmd -Visibility strict` |
| dynamic | `bash install.sh --visibility dynamic` | `.\install.cmd -Visibility dynamic` | `install.cmd -Visibility dynamic` |
| minimal | `bash install.sh --visibility minimal` | `.\install.cmd -Visibility minimal` | `install.cmd -Visibility minimal` |

`--agent-visibility`와 `-AgentVisibility`는 compatibility alias로 유지한다. 새 문서와 command에서는 `--visibility`, `-Visibility`를 우선한다.

### Slash Commands By Platform

Claude Code는 slash command를 first-class feature로 취급한다. Codex는 built-in slash command와 custom prompt path를 지원하지만, trusted runtime command가 없을 때 stable Ghost-ALICE profile 변경은 `_shared/agent_visibility_cli.py`를 사용한다.

### Python Contract

Installer는 Python 3.11 이상을 요구한다. Python 3.11+가 없으면 가능한 환경에서 automatic preparation을 시도한다.

- macOS: Homebrew가 있으면 `brew install python3`
- Linux / WSL: `apt-get`, `dnf`, `yum`, `pacman` 같은 package manager
- Windows: `winget`, `choco`, 그 다음 `scoop`

Python 3.11+가 여전히 없으면 installation을 멈추고 manual recovery guidance를 출력한다.

### Node.js Contract

Claude Code와 Codex hook-enabled install은 `tool-checkpoint` PreToolUse gate가 `ghost-alice-hook.mjs`를 실행하기 때문에 `PATH`의 Node.js를 요구한다. target platform이 있는데 `node`가 없으면 installer는 hook installation을 막는다.

### Platform Update Behavior

| Platform | OS | Install mode | Install path | Skill body updates auto-reflect |
| --- | --- | --- | --- | --- |
| Claude Code | macOS / Linux / WSL | symlink | `~/.claude/skills/` | yes |
| Claude Code | Windows | junction | `~/.claude/skills/` | yes |
| Codex | macOS / Linux / WSL | copy | `~/.agents/skills/` | no |
| Codex | Windows | copy | `~/.agents/skills/` | no |
| All platforms | Git Bash on Windows | copy fallback | varies | no |

auto-reflection은 `SKILL.md`, `references/`, `scripts/` 같은 skill body에 적용된다. Hooks, bootstrap files, permission policy, `_shared/`는 installer-managed runtime surface이므로 변경 시 installer를 다시 실행한다.

### Installed Surfaces

full install은 다음을 배포한다.

- `skill-catalog/skills.json`의 core skills
- coding-convention workflow skills
- official `--addon` aliases 또는 custom `--addon-source`로 설치하는 optional addon skills
- `_shared/` utilities
- platform hook settings
- `~/.ghost-alice/hooks/` 아래 Node-backed hook dispatcher assets
- Claude Code permission allowlist
- Codex `~/.codex/AGENTS.md` bootstrap
- install-state, pending merges, install rollbacks, uninstall reports support state

### merge-companion

update가 user-modified installed files를 감지하면 installer는 후보를 pending merge queue에 격리한다.

- manifest: `~/.ghost-alice/pending-merges/<platform>/manifest.json`
- backup: `~/.ghost-alice/pending-merges/<platform>/`
- install state: `~/.ghost-alice/install-state/<platform>.json`

다음 Claude/Codex session에서 pending entry가 있으면 `merge-companion`이 각 entry를 merge, discard, defer할지 묻는다.

## Uninstall

Uninstall은 installer-owned install-state manifest를 사용한다.

```bash
bash install.sh --uninstall
```

전체 cleanup contract는 [uninstall cleanup](./uninstall.md)을 본다.

## Troubleshooting

`git pull`, merge conflict, PowerShell installer rerun 중 update가 막히면 [troubleshooting](./troubleshooting.md)부터 본다.

같은 recovery playbook은 repo를 아직 pull할 수 없는 사람을 위해 GitHub Wiki `install-troubleshooting_ko` page에도 mirror된다.
