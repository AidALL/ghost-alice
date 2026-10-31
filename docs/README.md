# Documentation

언어: 🇺🇸 English | [🇰🇷 한국어](./ko/README.md)

Ghost-ALICE OS documentation is bilingual at the reader-facing documentation layer. English remains the default repository entry path. Korean counterparts live under `docs/ko/` with the same subdirectory shape.

Every paired document has a language switch near the top. The [language policy](concepts/language-policy.md) defines its format.

## Quick Install

From the cloned Core repository, install Core with automatic agent-platform detection:

```bash
bash install.sh
```

The [installation guide](./getting-started/installation.md) covers cloning, OS-native commands and explicit platform selection. An addon is installed from its own repository or a local path; see [Install Addons](./getting-started/installation.md#install-addons).

## Start Here

1. Install or update from [getting-started/installation.md](./getting-started/installation.md).
2. Recover blocked updates from [getting-started/troubleshooting.md](./getting-started/troubleshooting.md).
3. Understand the repository map from [reference/repository-structure.md](./reference/repository-structure.md).
4. Understand the installer architecture from [reference/installer-architecture.md](./reference/installer-architecture.md).
5. Use [SUPPORT.md](https://github.com/AidALL/ghost-alice/blob/main/SUPPORT.md) and GitHub Issues for public questions.
6. Use [SECURITY.md](https://github.com/AidALL/ghost-alice/blob/main/SECURITY.md) for private vulnerability reporting.

## Document Map

| Intent | English | Korean |
| --- | --- | --- |
| Architecture and responsibilities | [../architecture.md](../architecture.md) | [../architecture_ko.md](../architecture_ko.md) |
| Documentation index | docs/README.md | [ko/README.md](./ko/README.md) |
| Installation and update flow | [getting-started/installation.md](./getting-started/installation.md) | [ko/getting-started/installation.md](./ko/getting-started/installation.md) |
| Git/update troubleshooting | [getting-started/troubleshooting.md](./getting-started/troubleshooting.md) | [ko/getting-started/troubleshooting.md](./ko/getting-started/troubleshooting.md) |
| Uninstall cleanup | [getting-started/uninstall.md](./getting-started/uninstall.md) | [ko/getting-started/uninstall.md](./ko/getting-started/uninstall.md) |
| Repository structure | [reference/repository-structure.md](./reference/repository-structure.md) | [ko/reference/repository-structure.md](./ko/reference/repository-structure.md) |
| Installer architecture | [reference/installer-architecture.md](./reference/installer-architecture.md) | [ko/reference/installer-architecture.md](./ko/reference/installer-architecture.md) |
| Skill catalog guide | [reference/skills.md](./reference/skills.md) | [ko/reference/skills.md](./ko/reference/skills.md) |
| Official addons | [reference/official-addons.md](./reference/official-addons.md) | [ko/reference/official-addons.md](./ko/reference/official-addons.md) |
| Language policy | [concepts/language-policy.md](./concepts/language-policy.md) | [ko/concepts/language-policy.md](./ko/concepts/language-policy.md) |
| Runtime gate matrix | [policies/session-gate-matrix.md](./policies/session-gate-matrix.md) | [ko/policies/session-gate-matrix.md](./ko/policies/session-gate-matrix.md) |
| Platform compatibility | [policies/installer-platform-compatibility-matrix.md](./policies/installer-platform-compatibility-matrix.md) | [ko/policies/installer-platform-compatibility-matrix.md](./ko/policies/installer-platform-compatibility-matrix.md) |
| Tool output semantics | [policies/tool-output-semantics.md](./policies/tool-output-semantics.md) | [ko/policies/tool-output-semantics.md](./ko/policies/tool-output-semantics.md) |
| Platform adapter compliance | [policies/platform-adapter-compliance.md](./policies/platform-adapter-compliance.md) | [ko/policies/platform-adapter-compliance.md](./ko/policies/platform-adapter-compliance.md) |
| Live smoke regression | [policies/live-smoke-regression.md](./policies/live-smoke-regression.md) | [ko/policies/live-smoke-regression.md](./ko/policies/live-smoke-regression.md) |
| Evaluator artifact contract | [policies/evaluator-artifact-contract.md](./policies/evaluator-artifact-contract.md) | [ko/policies/evaluator-artifact-contract.md](./ko/policies/evaluator-artifact-contract.md) |
| Public release checklist | [release/public-release-checklist.md](./release/public-release-checklist.md) | [ko/release/public-release-checklist.md](./ko/release/public-release-checklist.md) |

## Canonical Contributor Documents

The [language policy](concepts/language-policy.md#intentionally-unpaired-sources) lists the sources that keep one English original; paired reader guides are listed above.

## Update Rule

Update this map whenever a user-facing document is added, moved, or paired.
