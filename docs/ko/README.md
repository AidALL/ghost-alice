# 문서 안내

Language: [🇺🇸 English](../README.md) | 🇰🇷 한국어

Ghost-ALICE OS의 사용자용 문서는 영어와 한국어로 제공합니다. 저장소의 기본 안내는 영어이며, 한국어 문서는 `docs/ko/` 아래에서 영어 문서와 같은 디렉터리 구조로 관리합니다.

쌍을 이루는 문서는 상단의 언어 전환 줄로 오가실 수 있습니다. 전환 줄 형식은 [언어 정책](concepts/language-policy.md)에서 정합니다.

Core `0.5.1`은 routing과 완료 검토 API를 제공합니다. 소스 검사, native runtime 검증과 공개 릴리즈는 서로 다른 근거를 제공합니다.

## 빠른 설치

복제한 Core 저장소에서 다음 명령을 실행하시면 인스톨러가 사용 가능한 에이전트 플랫폼을 자동으로 감지하여 Core를 설치합니다.

```bash
bash install.sh
```

저장소 복제, 운영체제별 명령과 명시적인 플랫폼 선택은 [설치 안내](./getting-started/installation.md)를 확인해 주세요. 애드온은 자체 저장소나 로컬 경로에서 설치합니다. [Install Addons](./getting-started/installation.md#install-addons)를 확인해 주세요.

## 시작 안내

1. 설치와 업데이트는 [getting-started/installation.md](./getting-started/installation.md)를 확인해 주세요.
2. 업데이트가 막히면 [getting-started/troubleshooting.md](./getting-started/troubleshooting.md)를 확인해 주세요.
3. 저장소 구조는 [reference/repository-structure.md](./reference/repository-structure.md)를 확인해 주세요.
4. 인스톨러 구조는 [reference/installer-architecture.md](./reference/installer-architecture.md)를 확인해 주세요.
5. 공개 질문은 [SUPPORT.md](https://github.com/AidALL/ghost-alice/blob/main/SUPPORT.md)와 GitHub Issues를 이용해 주세요.
6. 비공개 취약점 보고는 [SECURITY.md](https://github.com/AidALL/ghost-alice/blob/main/SECURITY.md)의 안내를 따라 주세요.

## 문서 안내 Layout

| 구분 | 역할 | 영어 | 한국어 |
| --- | --- | --- | --- |
| Getting Started | 설치·업데이트·복구·제거 절차 | [../getting-started/](../getting-started/installation.md) | [getting-started/](./getting-started/installation.md) |
| Concepts | 프로젝트 모델과 문서 언어 정책 | [../concepts/](../concepts/language-policy.md) | [concepts/](./concepts/language-policy.md) |
| Reference | 저장소 구조, 공개 스킬, 훅과 명령 안내 | [../reference/](../reference/repository-structure.md) | [reference/](./reference/repository-structure.md) |
| Policies | 실행 환경·플랫폼·평가 도구의 계약 | [../policies/](../policies/session-gate-matrix.md) | [policies/](./policies/session-gate-matrix.md) |
| Release | 공개 배포 검사와 검증 안내 | [../release/](../release/public-release-checklist.md) | [release/](./release/public-release-checklist.md) |

## 문서 목록

| 목적 | 영어 | 한국어 |
| --- | --- | --- |
| 아키텍처와 책임 | [../../architecture.md](../../architecture.md) | [../../architecture_ko.md](../../architecture_ko.md) |
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
| Public release checklist | [../release/public-release-checklist.md](../release/public-release-checklist.md) | [release/public-release-checklist.md](./release/public-release-checklist.md) |

## 기준 기여자 문서

영어 원본 하나만 유지하는 문서는 [언어 정책](concepts/language-policy.md#intentionally-unpaired-sources)이 나열하며, 사용자용 한국어 대응 문서는 위 표에서 제공합니다.

## 갱신 규칙

사용자용 문서를 추가하거나 이동하거나 언어별 대응 문서를 만들 때 이 문서 목록도 갱신해 주세요.
