# Installation And Update Guide

언어: [🇺🇸 English](../../getting-started/installation.md) | 🇰🇷 한국어

이 문서는 Ghost-ALICE OS 설치, 업데이트, 복구 절차를 자세히 설명한다. root README는 빠른 경로만 유지한다.
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

`install.cmd`는 PowerShell execution policy가 direct script execution 또는 profile loading을 막는 환경을 위한 wrapper다. 내부에서 `install.ps1`을 `-NoProfile -ExecutionPolicy Bypass`로 호출하고, 사용자 또는 머신 execution policy를 변경하지 않는다.

## Install One Platform

| Target | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| Claude Code | `bash install.sh --platform claude` | `.\install.cmd -Platform claude` | `install.cmd -Platform claude` |
| Codex | `bash install.sh --platform codex` | `.\install.cmd -Platform codex` | `install.cmd -Platform codex` |

interactive 선택은 `bash install.sh --prompt-platform`, `.\install.cmd -PromptPlatform`, `install.cmd -PromptPlatform`를 사용한다.

## Agent Visibility Profile

default profile은 `dynamic`다. 이 profile은 user에게 보이는 governance surface 양을
제어한다. hook, strict-grade logs, Work-Impact Projection을 disable하지 않는다.

| Profile | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| strict | `bash install.sh --visibility strict` | `.\install.cmd -Visibility strict` | `install.cmd -Visibility strict` |
| dynamic | `bash install.sh --visibility dynamic` | `.\install.cmd -Visibility dynamic` | `install.cmd -Visibility dynamic` |
| minimal | `bash install.sh --visibility minimal` | `.\install.cmd -Visibility minimal` | `install.cmd -Visibility minimal` |

`--agent-visibility`와 `-AgentVisibility`는 compatibility alias로 계속 받는다.
새 문서와 명령 예시는 `--visibility`와 `-Visibility`를 우선 사용한다.

## Slash Commands by Platform

Claude Code는 slash command를 first-class 기능으로 다룬다. `/visibility`와
skill `/name` invocation이 공식 지원되며, custom command는 현재
`.claude/commands/`와 `.claude/skills/<name>/SKILL.md` 형태를 사용한다.
참조: https://code.claude.com/docs/en/agent-sdk/slash-commands.

Codex도 `/plan` 같은 built-in slash command와 `~/.codex/prompts/` 아래의
custom prompt를 문서화하지만, custom-command surface는 version-dependent이고
custom prompt는 skills를 선호하는 방향으로 deprecated 상태다. 또한 Codex는
Claude처럼 instruction에서 slash command를 자동 호출하지 않는다. Codex에서는
trusted session에서 `/visibility`를 직접 입력하고, cross-platform reliable path로
`python3 _shared/agent_visibility_cli.py set <profile>`를 사용한다.
참조: https://developers.openai.com/codex/cli/slash-commands,
https://developers.openai.com/codex/custom-prompts.

## Python Contract

installer는 Python 3.11 이상을 요구한다. Python 3.11+가 없으면 가능한 환경에서 자동 준비를 시도한다.

- macOS: Homebrew가 있으면 `brew install python3`
- Linux / WSL: `apt-get`, `dnf`, `yum`, `pacman` 같은 package manager
- Windows: `winget`, `choco`, `scoop` 순서

Python 3.11+가 여전히 없으면 installation은 멈추고 manual recovery guidance를 출력한다.

## Node.js Contract

Claude Code와 Codex의 hook-enabled install은 `PATH`의 Node.js를 요구한다. `tool-checkpoint` PreToolUse gate가 `ghost-alice-hook.mjs`를 실행하기 때문이다. target platform이 존재하는데 `node`를 사용할 수 없으면 installer는 hook installation을 중단한다. 실행할 수 없는 Node-backed gate를 설치하지 않기 위한 계약이다.

## Check Status

설치 상태가 이상하면 doctor를 먼저 실행한다. Doctor는 read-only strict diagnostic이다.

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

healthy target은 `overall: ok`다.

## Update

먼저 installer로 local clone을 update한다. 이때 installer는 checkout을 fast-forward하기 전에 로컬 수정분을 `git stash`로 보존한다.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

checkout이 너무 오래되어 해당 option을 받을 수 없고 raw `git pull`이 이미 local changes로 막힌 경우 bootstrap updater를 사용한다. 이 command는 이미 설정된 Git remote를 통해 current updater를 가져오고, local changes를 `git stash`에 저장하고, `~/ghost-alice`를 fast-forward한 뒤 updated installer를 실행한다.

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

auto-reflection은 `SKILL.md`, `references/`, `scripts/` 같은 skill body에 적용된다. Hooks, bootstrap files, permission policy, `_shared/`는 installer-managed runtime surface이므로 변경 시 installer를 다시 실행한다.

## Installed Surfaces

full install은 다음을 배포한다.

- `skill-catalog/skills.json`의 core skills
- coding-convention workflow skills
- `--addon-source`가 제공될 때 optional addon skills
- `_shared/` utilities
- platform hook settings
- `~/.ghost-alice/hooks/` 아래 Node-backed hook dispatcher assets
- Claude Code permission allowlist
- Codex `~/.codex/AGENTS.md` bootstrap
- install-state, pending merges, install rollbacks, uninstall reports support state

## merge-companion

update가 user-modified installed files를 감지하면 installer는 후보를 pending merge queue에 격리한다.

- manifest: `~/.ghost-alice/pending-merges/<platform>/manifest.json`
- backup: `~/.ghost-alice/pending-merges/<platform>/`
- install state: `~/.ghost-alice/install-state/<platform>.json`

다음 Claude/Codex session에서 pending entry가 있으면 `merge-companion`이 각 entry를 merge, discard, defer할지 묻는다.

## Common Commands

| Purpose | macOS / Linux / WSL / Git Bash | Windows PowerShell | Windows CMD |
| --- | --- | --- | --- |
| List skills | `bash install.sh --list` | `.\install.cmd -List` | `install.cmd -List` |
| Show install state | `bash install.sh --status` | `.\install.cmd -Status` | `install.cmd -Status` |
| Run protected diagnostic | `bash install.sh --doctor` | `.\install.cmd -Doctor` | `install.cmd -Doctor` |
| Selective core install | `bash install.sh task-router verification-before-completion` | `.\install.cmd -Skills task-router,verification-before-completion` | `install.cmd -Skills task-router,verification-before-completion` |
| Full uninstall | `bash install.sh --uninstall` | `.\install.cmd -Uninstall` | `install.cmd -Uninstall` |
| Selective uninstall | `bash install.sh --platform codex --uninstall task-router` | `.\install.cmd -Platform codex -Uninstall -Skills task-router` | `install.cmd -Platform codex -Uninstall -Skills task-router` |
| Clean false pending entries | `bash install.sh --platform claude --cleanup-pending` | `.\install.cmd -Platform claude -CleanupPending` | `install.cmd -Platform claude -CleanupPending` |

## Uninstall

Uninstall은 installer-owned install-state manifest를 사용한다.

```bash
bash install.sh --uninstall
```

전체 cleanup contract는 [uninstall cleanup](./uninstall.md)을 본다.

## Troubleshooting

`git pull`, merge conflict, PowerShell installer rerun 중 update가 막히면 [troubleshooting](./troubleshooting.md)부터 본다.

같은 recovery playbook은 repo를 아직 pull할 수 없는 사람을 위해 GitHub Wiki `install-troubleshooting_ko` page에도 mirror된다.
