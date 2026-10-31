# Ghost-ALICE OS

언어: 🇺🇸 English | [🇰🇷 한국어](./README_ko.md)

![Ghost-ALICE OS logo](imgs/Ghost-ALICE_logo.png)

Ghost-ALICE OS is an agent governance layer for AI work. It keeps intent, boundaries, evidence, and runtime state inspectable across supported agent runtimes.

It is not a prompt library, a chatbot wrapper, or a standalone agent runtime. It is the operating layer that makes agent work auditable before the agent claims completion.

## Version 0.5.1

[Website](https://aidall.github.io/ghost-alice/) · [Architecture](./architecture.md)

Core 0.5.1 uses the current response-route and completion contract. Apache-2.0.

## How governed work proceeds

The responding model keeps the user's objective and constraints, performs authorized work, and judges whether contrary evidence affects a required result or dependency. It corrects affected work when needed and preserves supported results and unresolved requirements.

See the [Core architecture](./architecture.md) for the standalone flow and [session gate matrix](./docs/policies/session-gate-matrix.md) for the detailed contract.

## Quick Start

From the cloned Ghost-ALICE repository, install Core. The installer automatically detects the available agent platforms:

```bash
bash install.sh
```

See the [detailed installation guide](./docs/getting-started/installation.md) for cloning, OS-native entrypoints, platform selection, updates and troubleshooting.

## Addons

An addon is installed from its own repository or a local path:

```bash
bash install.sh --addon-source <repository-or-path>
```

For native commands, custom addon sources, platform selection and removal, use the [installation guide](./docs/getting-started/installation.md) and [addon reference](./docs/reference/official-addons.md).

## Documentation Map

The [documentation index](./docs/README.md) links the paired English and Korean documents. The [GitHub Wiki](https://github.com/AidALL/ghost-alice/wiki) explains the design principles and architecture, and its [Addon Authoring](https://github.com/AidALL/ghost-alice/wiki/Addon-Authoring) page explains how to write an addon.

## Project Guarantees

- Ghost-ALICE OS is a governance operating layer for agent sessions, not a general-purpose operating system.
- User intent, boundaries, verification criteria, and install state are first-class surfaces.
- The installer tracks managed surfaces and avoids broad deletion when ownership cannot be proven.

The [agent visibility profile](./docs/getting-started/installation.md#agent-visibility-profile) controls how much governance output the user sees; the installation guide gives its default and each platform's command.

See [troubleshooting](docs/getting-started/troubleshooting.md#intake-warning-and-doctor-lists) for the Ghost-ALICE intake warning and the doctor's intake lists.

Full uninstall:

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

## Contributing And Validation

Before changing skills, installer behavior, or public command surfaces, read [AGENTS.md](https://github.com/AidALL/ghost-alice/blob/main/AGENTS.md). Skill changes follow the [Skill Changes](./CONTRIBUTING.md#skill-changes) steps of the contributing guide.

Public surface validation:

```bash
python3 scripts/validate_public_surfaces.py
```

Session gate contract validation checks the repository contract in `skill-catalog/session-gates.json` and the user-facing policy surfaces:

```bash
python scripts/check_skill_gate_contract.py
```

The installer compatibility groups and their commands are in the [CI Commands](./docs/policies/installer-platform-compatibility-matrix.md#ci-commands) section of the installer platform compatibility matrix.

## License

Ghost-ALICE OS project-owned source code and documentation are licensed under the Apache License, Version 2.0. See [LICENSE](./LICENSE), [NOTICE](./NOTICE), and [THIRD_PARTY_NOTICES.md](https://github.com/AidALL/ghost-alice/blob/main/THIRD_PARTY_NOTICES.md).
