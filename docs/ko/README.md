# Documentation

언어: [🇺🇸 English](../README.md) | 🇰🇷 한국어

Ghost-ALICE OS 문서는 reader가 보는 layer에서 영어와 한국어 두 갈래로 둔다. 기본 entry는 English이고, 한국어 문서는 `docs/ko/` 아래에 영어 문서와 같은 디렉터리 구조로 둔다.

쌍을 이루는 문서는 모두 상단에 language switch를 둔다. 영어 문서에서는 English가 plain text, Korean이 link이고, 한국어 문서에서는 그 반대다.

## Start Here

1. 설치와 업데이트는 [getting-started/installation.md](./getting-started/installation.md)를 본다.
2. update가 막혔으면 [getting-started/troubleshooting.md](./getting-started/troubleshooting.md)를 본다.
3. repository map은 [reference/repository-structure.md](./reference/repository-structure.md)를 본다.
4. installer architecture는 [reference/installer-architecture.md](./reference/installer-architecture.md)를 본다.
5. 공개 질문은 [SUPPORT.md](../../SUPPORT.md)와 GitHub Issues를 사용한다.
6. 비공개 vulnerability report는 [SECURITY.md](../../SECURITY.md)를 따른다.

## Documentation Layout

| Area | Role | English | Korean |
| --- | --- | --- | --- |
| Getting Started | install, update, recovery, uninstall 절차 | [../getting-started/](../getting-started/) | [getting-started/](./getting-started/) |
| Concepts | project model과 documentation language policy | [../concepts/](../concepts/) | [concepts/](./concepts/) |
| Reference | repository map, public skills, hooks, command surfaces | [../reference/](../reference/) | [reference/](./reference/) |
| Policies | runtime, platform, evaluator contracts | [../policies/](../policies/) | [policies/](./policies/) |
| Release | public release checks와 validation guidance | [../release/](../release/) | [release/](./release/) |
| Plans | public planning boundaries와 roadmap notes | [../plans/](../plans/) | [plans/](./plans/) |

## Document Map

| Intent | English | Korean |
| --- | --- | --- |
| Documentation index | [../README.md](../README.md) | README.md |
| Installation and update flow | [../getting-started/installation.md](../getting-started/installation.md) | [getting-started/installation.md](./getting-started/installation.md) |
| Git/update troubleshooting | [../getting-started/troubleshooting.md](../getting-started/troubleshooting.md) | [getting-started/troubleshooting.md](./getting-started/troubleshooting.md) |
| Uninstall cleanup | [../getting-started/uninstall.md](../getting-started/uninstall.md) | [getting-started/uninstall.md](./getting-started/uninstall.md) |
| Repository structure | [../reference/repository-structure.md](../reference/repository-structure.md) | [reference/repository-structure.md](./reference/repository-structure.md) |
| Installer architecture | [../reference/installer-architecture.md](../reference/installer-architecture.md) | [reference/installer-architecture.md](./reference/installer-architecture.md) |
| Skill catalog guide | [../reference/skills.md](../reference/skills.md) | [reference/skills.md](./reference/skills.md) |
| Language policy | [../concepts/language-policy.md](../concepts/language-policy.md) | [concepts/language-policy.md](./concepts/language-policy.md) |
| Runtime gate matrix | [../policies/session-gate-matrix.md](../policies/session-gate-matrix.md) | [policies/session-gate-matrix.md](./policies/session-gate-matrix.md) |
| Platform compatibility | [../policies/installer-platform-compatibility-matrix.md](../policies/installer-platform-compatibility-matrix.md) | [policies/installer-platform-compatibility-matrix.md](./policies/installer-platform-compatibility-matrix.md) |
| Tool output semantics | [../policies/tool-output-semantics.md](../policies/tool-output-semantics.md) | [policies/tool-output-semantics.md](./policies/tool-output-semantics.md) |
| Platform adapter compliance | [../policies/platform-adapter-compliance.md](../policies/platform-adapter-compliance.md) | [policies/platform-adapter-compliance.md](./policies/platform-adapter-compliance.md) |
| Live smoke regression | [../policies/live-smoke-regression.md](../policies/live-smoke-regression.md) | [policies/live-smoke-regression.md](./policies/live-smoke-regression.md) |
| Evaluator artifact contract | [../policies/evaluator-artifact-contract.md](../policies/evaluator-artifact-contract.md) | [policies/evaluator-artifact-contract.md](./policies/evaluator-artifact-contract.md) |
| Public release checklist | [../release/public-release-checklist.md](../release/public-release-checklist.md) | [release/public-release-checklist.md](./release/public-release-checklist.md) |
| Planning policy | [../plans/README.md](../plans/README.md) | [plans/README.md](./plans/README.md) |

## Update Rule

1. English default page를 먼저 수정한다.
2. reader-facing meaning, path, command, policy가 바뀌면 같은 change에서 Korean counterpart를 수정한다.
3. CLI flags, paths, hook names, skill names, enum values, schema fields는 literal로 유지한다.
4. 주변 설명만 번역하고 executable token은 보존한다.
5. English 문서는 English 문서끼리, Korean 문서는 counterpart가 있을 때 Korean counterpart끼리 링크한다.
6. user-facing document가 추가, 이동, pairing될 때 이 map을 갱신한다.
