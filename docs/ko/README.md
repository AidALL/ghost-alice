# Documentation

언어: [🇺🇸 English](../README.md) | 🇰🇷 한국어

Ghost-ALICE OS의 사용자용 문서는 영어와 한국어로 제공합니다. 저장소의 기본 안내는 영어이며, 한국어 문서는 `docs/ko/` 아래에서 영어 문서와 같은 디렉터리 구조로 관리합니다.

쌍을 이루는 문서는 상단에서 언어를 전환하실 수 있습니다. 영어 문서에서는 한국어 링크를, 한국어 문서에서는 영어 링크를 제공합니다.

현재 버전을 맞춘 공개 릴리스는 Ghost-ALICE OS `0.4.1`과 Ghost-ALICE Autopilot `0.4.1`입니다. 의도·세션·연속 실행 변경과 검증 범위는 [릴리스 노트](./release/2026-10-01-release-notes.md)에서 확인하실 수 있습니다.

## 빠른 설치

복제한 Core 저장소에서 다음 명령을 실행하시면 사용 가능한 에이전트 플랫폼을 자동으로 감지하여 Core와 Autopilot을 함께 설치합니다.

```bash
bash install.sh --addon autopilot
```

상태 질문과 교정 중에도 승인된 작업의 범위를 유지하며 작업을 이어가고, 완료 보고를 항목별 근거와 연결합니다. 저장소 복제, 운영체제별 명령과 명시적인 플랫폼 선택은 [설치 안내](./getting-started/installation.md)를 확인해 주세요.

## Start Here

1. 설치와 업데이트는 [getting-started/installation.md](./getting-started/installation.md)를 확인해 주세요.
2. 업데이트가 막히면 [getting-started/troubleshooting.md](./getting-started/troubleshooting.md)를 확인해 주세요.
3. 저장소 구조는 [reference/repository-structure.md](./reference/repository-structure.md)를 확인해 주세요.
4. 설치기 구조는 [reference/installer-architecture.md](./reference/installer-architecture.md)를 확인해 주세요.
5. 공개 질문은 [SUPPORT.md](../../SUPPORT.md)와 GitHub Issues를 이용해 주세요.
6. 비공개 취약점 보고는 [SECURITY.md](../../SECURITY.md)의 안내를 따라 주세요.

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
| Official addons | [../reference/official-addons.md](../reference/official-addons.md) | [reference/official-addons.md](./reference/official-addons.md) |
| Language policy | [../concepts/language-policy.md](../concepts/language-policy.md) | [concepts/language-policy.md](./concepts/language-policy.md) |
| Runtime gate matrix | [../policies/session-gate-matrix.md](../policies/session-gate-matrix.md) | [policies/session-gate-matrix.md](./policies/session-gate-matrix.md) |
| Platform compatibility | [../policies/installer-platform-compatibility-matrix.md](../policies/installer-platform-compatibility-matrix.md) | [policies/installer-platform-compatibility-matrix.md](./policies/installer-platform-compatibility-matrix.md) |
| Tool output semantics | [../policies/tool-output-semantics.md](../policies/tool-output-semantics.md) | [policies/tool-output-semantics.md](./policies/tool-output-semantics.md) |
| Platform adapter compliance | [../policies/platform-adapter-compliance.md](../policies/platform-adapter-compliance.md) | [policies/platform-adapter-compliance.md](./policies/platform-adapter-compliance.md) |
| Live smoke regression | [../policies/live-smoke-regression.md](../policies/live-smoke-regression.md) | [policies/live-smoke-regression.md](./policies/live-smoke-regression.md) |
| Evaluator artifact contract | [../policies/evaluator-artifact-contract.md](../policies/evaluator-artifact-contract.md) | [policies/evaluator-artifact-contract.md](./policies/evaluator-artifact-contract.md) |
| Current release notes | [../release/2026-10-01-release-notes.md](../release/2026-10-01-release-notes.md) | [release/2026-10-01-release-notes.md](./release/2026-10-01-release-notes.md) |
| Public release checklist | [../release/public-release-checklist.md](../release/public-release-checklist.md) | [release/public-release-checklist.md](./release/public-release-checklist.md) |
| Planning policy | [../plans/README.md](../plans/README.md) | [plans/README.md](./plans/README.md) |

## 기준 기여자 문서

`CONTRIBUTING.md`, `SUPPORT.md`, `CHANGELOG.md`와 `official-docs/`는 영어를 기준 기여자 경로로 사용합니다. 제어 토큰과 과거 릴리즈 기록은 그대로 보존하며, 현재 사용자용 한국어 문서는 위 표에서 제공합니다. 과거 릴리즈 안내는 해당 날짜의 버전과 검증 한계를 보존합니다.

## Update Rule

1. 기본 영문 페이지를 먼저 수정해 주세요.
2. 사용자에게 전달되는 의미, 경로, 명령, 정책이 바뀌면 같은 변경에서 한국어 대응 문서도 수정해 주세요.
3. CLI 옵션, 경로, 훅 이름, 스킬 이름, 열거 값, 스키마 필드는 원래 표기를 유지해 주세요.
4. 주변 설명만 번역하고 실행에 사용되는 토큰은 보존해 주세요.
5. 영어 문서는 영어 문서로 연결하고, 한국어 문서는 대응 문서가 있으면 한국어 문서로 연결해 주세요.
6. 사용자용 문서를 추가하거나 이동하거나 언어별 대응 문서를 만들 때 이 문서 목록도 갱신해 주세요.
