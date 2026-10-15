# Repository Structure

Language: [🇺🇸 English](../../reference/repository-structure.md) | 🇰🇷 한국어

README는 프로젝트 소개와 빠른 시작을 안내합니다. 이 문서는 각 구성 요소의 파일 위치와 역할을 자세히 안내합니다.
## Contents

- [Top-Level Documents](#top-level-documents)
- [Installer And Platforms](#installer-and-platforms)
- [Shared Utilities](#shared-utilities)
- [Core Governance And Runtime Gates](#core-governance-and-runtime-gates)
- [Official Docs And Policies](#official-docs-and-policies)
- [Installed Runtime State](#installed-runtime-state)
- [Documentation Responsibility](#documentation-responsibility)


## Top-Level Documents

| Path | Role |
| --- | --- |
| [README.md](../../../README.md) | 프로젝트 소개·빠른 시작과 문서 목록 |
| [README_ko.md](../../../README_ko.md) | 한국어 프로젝트 소개와 문서 목록 |
| [architecture.md](../../../architecture.md), [architecture_ko.md](../../../architecture_ko.md) | 양 언어 Core 책임·데이터 흐름 안내 |
| [AGENTS.md](../../../AGENTS.md) | 현재 호스트 계약의 프로젝트 로더 |
| [공통 거버넌스](../../../platforms/common-governance.md) | 호스트별 규칙과 결합하는 공통 계약 원본 |
| [CLAUDE.md](https://github.com/AidALL/ghost-alice/blob/main/CLAUDE.md) | Claude Code 프로젝트 규칙의 진입점 |
| [docs/index.html](../../index.html) | GitHub Pages 홈페이지 |

## Installer And Platforms

| Path | Role |
| --- | --- |
| [install.sh](https://github.com/AidALL/ghost-alice/blob/main/install.sh) | macOS·Linux·WSL·Git Bash 설치 진입점 |
| [install.ps1](https://github.com/AidALL/ghost-alice/blob/main/install.ps1) | Windows PowerShell 설치 진입점 |
| [install.cmd](https://github.com/AidALL/ghost-alice/blob/main/install.cmd) | Windows CMD에서 인수를 전달하는 실행 파일 |
| [platforms/codex/](https://github.com/AidALL/ghost-alice/tree/main/platforms/codex) | Codex 부트스트랩의 원본 |
| [hooks/](https://github.com/AidALL/ghost-alice/tree/main/hooks) | 저장소 훅 관련 파일 |

설치기 전체 구조는 [설치기 아키텍처](./installer-architecture.md)를 확인해 주세요.

## Shared Utilities

| Path | Role |
| --- | --- |
| [_shared/](https://github.com/AidALL/ghost-alice/tree/main/_shared) | 설치기와 스킬이 공유하는 도우미 |
| [_shared/secrets/](https://github.com/AidALL/ghost-alice/tree/main/_shared/secrets) | 외부 자격 증명을 조회하는 도우미 |
| [_shared/mcp/](https://github.com/AidALL/ghost-alice/tree/main/_shared/mcp) | MCP 설치 도우미 |
| [scripts/](https://github.com/AidALL/ghost-alice/tree/main/scripts) | 카탈로그 생성·검증·설치기 호환성·공개 안내 검증 스크립트 |

## Core Governance And Runtime Gates

| Path | Role |
| --- | --- |
| [session-intent-analyzer/](https://github.com/AidALL/ghost-alice/tree/main/session-intent-analyzer) | 사용자 의도 사실과 결정을 기록하는 공식 입력 절차 |
| [task-router/](https://github.com/AidALL/ghost-alice/tree/main/task-router) | 정확한 세션 의도를 읽고 현재 작업과 스킬을 선택하는 Core 절차 |
| [boundary-contract/](https://github.com/AidALL/ghost-alice/tree/main/boundary-contract) | 작업의 목표·허용 범위·금지 대상·중단 조건을 선언하는 절차 |
| [coding-convention/](https://github.com/AidALL/ghost-alice/tree/main/coding-convention) | 개발 작업 절차를 제공하는 스킬 모음 |
| [skill-catalog/](https://github.com/AidALL/ghost-alice/tree/main/skill-catalog) | 실행 환경이 읽는 생성된 검사 메타데이터 |
| [_shared/native_completion.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/native_completion.py) | 완료 guard와 Ghost-ALICE Autopilot이 함께 사용하는 Core의 단일 평가 |
| [_shared/original_claim_evidence.py](https://github.com/AidALL/ghost-alice/blob/main/_shared/original_claim_evidence.py) | 보존된 원본 주장 연결을 현재 기준·의존 관계에 대조하여 인증합니다 |

## Official Docs And Policies

| Path | Role |
| --- | --- |
| [official-docs/derived/](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived) | 공개 기여자가 따라야 하는 준수 절차이며 장문 철학은 Wiki에서 제공합니다 |
| [docs/policies/](https://github.com/AidALL/ghost-alice/tree/main/docs/policies) | 영어 실행 환경·플랫폼·평가 정책 문서 |
| [docs/ko/policies/](https://github.com/AidALL/ghost-alice/tree/main/docs/ko/policies) | 정책 문서의 한국어 대응 자료 |

## Installed Runtime State

설치기는 다음 자료를 저장소 밖의 사용자 홈 디렉터리에서 관리합니다.

| Path | Role |
| --- | --- |
| `~/.ghost-alice/install-state/` | 설치기가 기록한 플랫폼별 설치 상태 |
| `~/.ghost-alice/pending-merges/` | 설치기가 보호한 사용자 수정 파일의 병합 대기 자료 |
| `~/.ghost-alice/uninstall-reports/` | 제거 명령이 작성한 결과 |
| `~/.ghost-alice/secrets.env` | 표준 자격 증명 도우미의 저장소 |
| `~/.agents/skills/` | Codex용 사용자 스킬 복사 설치 위치 |
| `~/.claude/skills/` | Claude Code용 스킬 설치 위치 |

플랫폼 설치본은 모든 프로젝트에 공통 거버넌스를 제공합니다. Codex의 관리 로더는 `~/.codex/ghost-alice-governance.md`를 지정하며, Claude의 관리되는 전역 `CLAUDE.md`는 공통 계약을 담습니다. 프로젝트 지침 파일은 프로젝트 고유 제약을 제공합니다. [설치 안내](../getting-started/installation.md#공통-거버넌스와-프로젝트-지침)는 소스·설치본·응답 모델 사이의 전달을 설명합니다. 세션 실행 상태의 기준은 영수증의 저장소 루트에 있는 `.tmp/session-intent/ghost-state.sqlite3`입니다. JSON 포인터와 스냅샷은 호환성·내보내기 자료입니다. 상태를 읽는 구성 요소는 영수증의 root·platform·session identity를 `session_intent_ledger.py --read-state`에 전달하여 해당 세션의 상태를 받습니다.

## Documentation Responsibility

- README는 프로젝트 소개와 빠른 시작을 담습니다.
- 자세한 설치·업데이트 안내는 [installation guide](../getting-started/installation.md)에 둡니다.
- 기여자용 설치 흐름과 보호 규칙은 [installer architecture](./installer-architecture.md)에 둡니다.
- 정책과 실행 환경별 계약 표는 [docs/ko/policies](https://github.com/AidALL/ghost-alice/tree/main/docs/ko/policies)에 둡니다.
- 장문 철학과 결과를 판단에 다시 반영하는 작업 흐름은 [Wiki](https://github.com/AidALL/ghost-alice/wiki/홈)에 두며 [official-docs/derived](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived)는 공개 기여자 절차를 담습니다.
