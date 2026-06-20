# Ghost-ALICE OS

언어: [English](./README.md) | Korean

![Ghost-ALICE OS logo](imgs/Ghost-ALICE_logo.png)

Ghost-ALICE OS는 AI 작업을 위한 agent governance layer다. 지원하는 agent runtime에서 intent, boundary, evidence, runtime state를 inspectable하게 유지한다.

이 repository는 prompt library, chatbot wrapper, standalone agent runtime이 아니다. agent가 completion을 주장하기 전에 작업을 audit 가능하게 만드는 operating layer다.

## Quick Start

```bash
git clone https://github.com/AidALL/ghost-alice.git ~/ghost-alice
cd ~/ghost-alice
bash install.sh
```

Windows PowerShell / CMD:

```powershell
.\install.cmd
```

`cmd.exe`에서는 앞의 `.\`를 생략한다.

```cmd
install.cmd
```

`install.cmd`는 PowerShell execution policy 또는 profile loading이 direct `.ps1` execution을 막는 Windows shell을 위한 얇은 wrapper다. Windows install path는 Python 3.11+ runtime contract와 UTF-8 console setup을 `install.ps1`에 유지한다. wrapper는 `-NoProfile -ExecutionPolicy Bypass`로 호출하고 argument를 전달한다. 사용자 또는 머신 execution policy를 변경하지 않는다.

## Official Addons

official addon은 core checkout에서 alias로 설치한다. official addon은 별도 repository로 유지되고 Ghost-ALICE core checkout에서 짧은 alias로 설치한다.

```bash
bash install.sh --addon <addon>
```

```powershell
.\install.cmd --addon <addon>
```

일부 official addon은 capability skill만 추가한다. 다른 official addon은 runtime workflow를 확장할 수 있다. addon-specific behavior, state files, pause/resume controls, removal details 같은 addon-specific detail은 addon repository에 둔다.

| Addon | Purpose | Basic install | Details |
| --- | --- | --- | --- |
| autopilot | explicitly approved autonomous run을 work item 단위로 계속 진행한다 | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

custom, tenant, local development addon은 `--addon-source`를 사용한다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

## Documentation Map

| Need | Go to |
| --- | --- |
| 전체 installation/update command | [Installation and update guide](./docs/ko/getting-started/installation.md) |
| uninstall scope와 cleanup behavior | [Uninstall cleanup procedure](./docs/ko/getting-started/uninstall.md) |
| update 실패, merge conflict, reinstall recovery | [Troubleshooting](./docs/ko/getting-started/troubleshooting.md) |
| repository layout | [Repository structure](./docs/ko/reference/repository-structure.md) |
| skill catalog reference | [Skill catalog guide](./docs/ko/reference/skills.md) |
| session gate contract | [Session gate matrix](./docs/ko/policies/session-gate-matrix.md) |
| installer compatibility contract | [Installer platform compatibility](./docs/ko/policies/installer-platform-compatibility-matrix.md) |
| team onboarding과 background | [GitHub Wiki](https://github.com/AidALL/ghost-alice/wiki) |
| official addon usage | [Wiki: official addons](https://github.com/AidALL/ghost-alice/wiki/official-addons_ko) |
| addon authoring | [Wiki: addon authoring](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko) |

## Project Guarantees

- Ghost-ALICE OS는 agent session을 위한 governance operating layer이며 general-purpose operating system이 아니다.
- User intent, boundaries, verification criteria, install state는 first-class surface다.
- Session routing은 tool execution 전에 recorded intent와 downstream gate state를 소비한다.
- 실행, 수정, 검증이 끝났다고 주장하려면 fresh evidence가 필요하다.
- Installer는 managed surface를 추적하고 ownership을 증명할 수 없으면 deletion 범위를 넓히지 않는다.

Agent visibility command surface:

- Claude Code는 workspace command로 `/visibility strict|dynamic|minimal`을 사용한다.
- Codex는 trusted `UserPromptSubmit` hook pseudo-command path를 통해 `/visibility`를 처리한다.
- 모든 platform은 `_shared/agent_visibility_cli.py`를 통해 같은 profile 값을 확인하고 변경할 수 있다.
- install-time default는 `dynamic`다. initial profile은 `bash install.sh --visibility dynamic` 또는 `.\install.cmd -Visibility dynamic`으로 설정한다. `--agent-visibility`와 `-AgentVisibility`는 compatibility alias로 유지한다.
- Visibility는 user-facing governance message surface만 바꾼다. hook execution, strict-grade logs, Work-Impact Projection은 약화하지 않는다.

Full uninstall:

```bash
bash install.sh --uninstall
```

```powershell
.\install.cmd -Uninstall
```

```cmd
install.cmd -Uninstall
```

## Contributing And Validation

skill, installer behavior, public command surface를 바꾸기 전에는 [AGENTS.md](./AGENTS.md)를 읽는다. 새 skill을 만들거나 기존 skill을 수정한 뒤에는 [official-docs/derived/skill-compliance-checklist.md](./official-docs/derived/skill-compliance-checklist.md)의 Phase 1-5를 통과해야 한다.

Public surface validation:

```bash
python3 scripts/validate_public_surfaces.py
```

Session gate contract validation은 repository contract인
`skill-catalog/session-gates.json`과 user-facing policy surface를 검사한다.

```bash
python scripts/check_skill_gate_contract.py
```

Installer compatibility groups:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
```

## License

Ghost-ALICE OS project-owned source code와 documentation은 Apache License,
Version 2.0으로 license된다. [LICENSE](./LICENSE), [NOTICE](./NOTICE),
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)를 본다.
