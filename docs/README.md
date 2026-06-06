# Documentation

Language: 🇺🇸 English | [🇰🇷 한국어](./ko/README.md)

Ghost-ALICE OS documentation is bilingual at the reader-facing documentation layer. English remains the default repository entry path. Korean counterparts live under `docs/ko/` with the same subdirectory shape.

Every paired document should expose a language switch near the top. In an English document, the English side is plain text and the Korean side is a link. In a Korean document, the English side is a link and the Korean side is plain text.

## Start Here

1. Install or update from [getting-started/installation.md](./getting-started/installation.md).
2. Recover blocked updates from [getting-started/troubleshooting.md](./getting-started/troubleshooting.md).
3. Understand the repository map from [reference/repository-structure.md](./reference/repository-structure.md).
4. Understand the installer architecture from [reference/installer-architecture.md](./reference/installer-architecture.md).
5. Use [SUPPORT.md](../SUPPORT.md) and GitHub Issues for public questions.
6. Use [SECURITY.md](../SECURITY.md) for private vulnerability reporting.

## Documentation Layout

| Area | Role | English | Korean |
| --- | --- | --- | --- |
| Getting Started | Install, update, recovery, and uninstall procedures | [getting-started/](./getting-started/) | [ko/getting-started/](./ko/getting-started/) |
| Concepts | Project model and documentation language policy | [concepts/](./concepts/) | [ko/concepts/](./ko/concepts/) |
| Reference | Repository map, public skills, hooks, and command surfaces | [reference/](./reference/) | [ko/reference/](./ko/reference/) |
| Policies | Runtime, platform, and evaluator contracts | [policies/](./policies/) | [ko/policies/](./ko/policies/) |
| Release | Public release checks and validation guidance | [release/](./release/) | [ko/release/](./ko/release/) |
| Plans | Public planning boundaries and roadmap notes | [plans/](./plans/) | [ko/plans/](./ko/plans/) |

## Document Map

| Intent | English | Korean |
| --- | --- | --- |
| Documentation index | docs/README.md | [ko/README.md](./ko/README.md) |
| Installation and update flow | [getting-started/installation.md](./getting-started/installation.md) | [ko/getting-started/installation.md](./ko/getting-started/installation.md) |
| Git/update troubleshooting | [getting-started/troubleshooting.md](./getting-started/troubleshooting.md) | [ko/getting-started/troubleshooting.md](./ko/getting-started/troubleshooting.md) |
| Uninstall cleanup | [getting-started/uninstall.md](./getting-started/uninstall.md) | [ko/getting-started/uninstall.md](./ko/getting-started/uninstall.md) |
| Repository structure | [reference/repository-structure.md](./reference/repository-structure.md) | [ko/reference/repository-structure.md](./ko/reference/repository-structure.md) |
| Installer architecture | [reference/installer-architecture.md](./reference/installer-architecture.md) | [ko/reference/installer-architecture.md](./ko/reference/installer-architecture.md) |
| Skill catalog guide | [reference/skills.md](./reference/skills.md) | [ko/reference/skills.md](./ko/reference/skills.md) |
| Language policy | [concepts/language-policy.md](./concepts/language-policy.md) | [ko/concepts/language-policy.md](./ko/concepts/language-policy.md) |
| Runtime gate matrix | [policies/session-gate-matrix.md](./policies/session-gate-matrix.md) | [ko/policies/session-gate-matrix.md](./ko/policies/session-gate-matrix.md) |
| Platform compatibility | [policies/installer-platform-compatibility-matrix.md](./policies/installer-platform-compatibility-matrix.md) | [ko/policies/installer-platform-compatibility-matrix.md](./ko/policies/installer-platform-compatibility-matrix.md) |
| Tool output semantics | [policies/tool-output-semantics.md](./policies/tool-output-semantics.md) | [ko/policies/tool-output-semantics.md](./ko/policies/tool-output-semantics.md) |
| Platform adapter compliance | [policies/platform-adapter-compliance.md](./policies/platform-adapter-compliance.md) | [ko/policies/platform-adapter-compliance.md](./ko/policies/platform-adapter-compliance.md) |
| Live smoke regression | [policies/live-smoke-regression.md](./policies/live-smoke-regression.md) | [ko/policies/live-smoke-regression.md](./ko/policies/live-smoke-regression.md) |
| Evaluator artifact contract | [policies/evaluator-artifact-contract.md](./policies/evaluator-artifact-contract.md) | [ko/policies/evaluator-artifact-contract.md](./ko/policies/evaluator-artifact-contract.md) |
| Public release checklist | [release/public-release-checklist.md](./release/public-release-checklist.md) | [ko/release/public-release-checklist.md](./ko/release/public-release-checklist.md) |
| Planning policy | [plans/README.md](./plans/README.md) | [ko/plans/README.md](./ko/plans/README.md) |

## Update Rule

1. Update the English default page first.
2. Update the Korean counterpart in the same change when the reader-facing meaning, path, command, or policy changes.
3. Keep CLI flags, paths, hook names, skill names, enum values, and schema fields literal.
4. Translate the surrounding explanation and preserve executable tokens.
5. Keep English documents linked to English documents and Korean documents linked to Korean counterparts when a counterpart exists.
6. Update this map whenever a user-facing document is added, moved, or paired.
