# Ghost-ALICE OS

Language: English | [Korean](./README_ko.md)

![Ghost-ALICE OS logo](imgs/Ghost-ALICE_logo.png)

Ghost-ALICE OS is an agent governance layer for AI work. It keeps intent, boundaries, evidence, and runtime state inspectable across supported agent runtimes.

It is not a prompt library, a chatbot wrapper, or a standalone agent runtime. It is the operating layer that makes agent work auditable before the agent claims completion.

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

For `cmd.exe`, omit the leading `.\`.

```cmd
install.cmd
```

`install.cmd` is a thin wrapper around `install.ps1` for Windows shells where PowerShell execution policy or profile loading blocks direct `.ps1` execution. The Windows install path keeps the Python 3.11+ runtime contract and UTF-8 console setup in `install.ps1`; the wrapper calls it with `-NoProfile -ExecutionPolicy Bypass` and forwards arguments. It does not change the user or machine execution policy.

## Official Addons

Official addons are maintained as separate repositories and installed from the Ghost-ALICE core checkout with a short alias.

```bash
bash install.sh --addon <addon>
```

```powershell
.\install.cmd --addon <addon>
```

Some official addons add only capability skills. Others can extend runtime workflow behavior. Addon-specific behavior, state files, pause/resume controls, and removal details live in each addon repository.

| Addon | Purpose | Basic install | Details |
| --- | --- | --- | --- |
| autopilot | Continue explicitly approved autonomous runs one work item at a time | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

Custom, tenant, or local development addons use `--addon-source`.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

## Documentation Map

| Need | Go to |
| --- | --- |
| Full installation and update commands | [Installation and update guide](./docs/getting-started/installation.md) |
| Uninstall scope and cleanup behavior | [Uninstall cleanup procedure](./docs/getting-started/uninstall.md) |
| Failed update, merge conflict, or reinstall recovery | [Troubleshooting](./docs/getting-started/troubleshooting.md) |
| Repository layout | [Repository structure](./docs/reference/repository-structure.md) |
| Skill catalog reference | [Skill catalog guide](./docs/reference/skills.md) |
| Session gate contract | [Session gate matrix](./docs/policies/session-gate-matrix.md) |
| Installer compatibility contract | [Installer platform compatibility](./docs/policies/installer-platform-compatibility-matrix.md) |
| Team onboarding and background | [GitHub Wiki](https://github.com/AidALL/ghost-alice/wiki) |
| Official addon usage | [Wiki: official addons](https://github.com/AidALL/ghost-alice/wiki/official-addons) |
| Addon authoring | [Wiki: addon authoring](https://github.com/AidALL/ghost-alice/wiki/addon-authoring) |

## Project Guarantees

- Ghost-ALICE OS is a governance operating layer for agent sessions, not a general-purpose operating system.
- User intent, boundaries, verification criteria, and install state are first-class surfaces.
- Session routing consumes recorded intent and downstream gate state before tool execution.
- Completion claims require fresh evidence when the work has been executed, fixed, or verified.
- The installer tracks managed surfaces and avoids broad deletion when ownership cannot be proven.

Agent visibility command surface:

- Claude Code uses `/visibility strict|dynamic|minimal` as its workspace command.
- Codex handles `/visibility` through the trusted `UserPromptSubmit` hook pseudo-command path.
- Every platform can inspect and change the same profile value through `_shared/agent_visibility_cli.py`.
- The install-time default is `dynamic`. Use `bash install.sh --visibility dynamic` or `.\install.cmd -Visibility dynamic` to set the initial profile; `--agent-visibility` and `-AgentVisibility` remain compatibility aliases.
- Visibility changes the user-facing governance message surface only. It does not weaken hook execution, strict-grade logs, or Work-Impact Projection.

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

Before changing skills, installer behavior, or public command surfaces, read [AGENTS.md](./AGENTS.md). After creating or modifying a skill, pass Phases 1-5 of [official-docs/derived/skill-compliance-checklist.md](./official-docs/derived/skill-compliance-checklist.md).

Public surface validation:

```bash
python3 scripts/validate_public_surfaces.py
```

Session gate contract validation checks the repository contract in
`skill-catalog/session-gates.json` and the user-facing policy surfaces:

```bash
python scripts/check_skill_gate_contract.py
```

Installer compatibility groups:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
```

## License

Ghost-ALICE OS project-owned source code and documentation are licensed under
the Apache License, Version 2.0. See [LICENSE](./LICENSE), [NOTICE](./NOTICE),
and [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
