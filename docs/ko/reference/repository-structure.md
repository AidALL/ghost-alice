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
- [Runtime State Retention](#runtime-state-retention)
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
| [install.ps1](https://github.com/AidALL/ghost-alice/blob/main/install.ps1) | PowerShell 7.6 이상용 Windows 설치 진입점 |
| [install.cmd](https://github.com/AidALL/ghost-alice/blob/main/install.cmd) | Windows CMD에서 인수를 전달하는 실행 파일 |
| [platforms/codex/](https://github.com/AidALL/ghost-alice/tree/main/platforms/codex) | Codex 부트스트랩의 원본 |
| [hooks/](https://github.com/AidALL/ghost-alice/tree/main/hooks) | 저장소 훅 관련 파일 |

인스톨러 전체 구조는 [인스톨러 아키텍처](./installer-architecture.md)를 확인해 주세요.

## Shared Utilities

| Path | Role |
| --- | --- |
| [_shared/](https://github.com/AidALL/ghost-alice/tree/main/_shared) | 인스톨러와 스킬이 공유하는 도우미 |
| [_shared/secrets/](https://github.com/AidALL/ghost-alice/tree/main/_shared/secrets) | 외부 자격 증명을 조회하는 도우미 |
| [_shared/mcp/](https://github.com/AidALL/ghost-alice/tree/main/_shared/mcp) | MCP 설치 도우미 |
| [scripts/](https://github.com/AidALL/ghost-alice/tree/main/scripts) | 카탈로그 생성·검증·인스톨러 호환성·공개 안내 검증 스크립트 |

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

인스톨러는 다음 자료를 저장소 밖의 사용자 홈 디렉터리에서 관리합니다.

| Path | Role |
| --- | --- |
| `~/.ghost-alice/install-state/` | 인스톨러가 기록한 플랫폼별 설치 상태 |
| `~/.ghost-alice/pending-merges/` | 인스톨러가 보호한 사용자 수정 파일의 병합 대기 자료 |
| `~/.ghost-alice/uninstall-reports/` | 제거 명령이 작성한 결과 |
| `~/.ghost-alice/secrets.env` | 표준 자격 증명 도우미의 저장소 |
| `~/.agents/skills/` | Codex용 사용자 스킬 복사 설치 위치 |
| `~/.claude/skills/` | Claude Code용 스킬 설치 위치 |

플랫폼 설치본은 모든 프로젝트에 공통 거버넌스를 제공합니다. Codex의 관리 로더는 `~/.codex/ghost-alice-governance.md`를 지정하며, Claude의 관리되는 전역 `CLAUDE.md`는 공통 계약을 담습니다. 프로젝트 지침 파일은 프로젝트 고유 제약을 제공합니다. [설치 안내](../getting-started/installation.md#공통-거버넌스와-프로젝트-지침)는 소스·설치본·응답 모델 사이의 전달을 설명합니다. 세션 실행 상태의 기준은 영수증의 저장소 루트에 있는 `.tmp/session-intent/ghost-state.sqlite3`입니다. JSON 포인터와 스냅샷은 호환성·내보내기 자료입니다. 상태를 읽는 구성 요소는 영수증의 root·platform·session identity를 `session_intent_ledger.py --read-state`에 전달하여 해당 세션의 상태를 받습니다.

## Runtime State Retention

`_shared/state_retention.py`는 Core 설치가 `~/.ghost-alice`와 호스트 폴더 아래에 작성하는 파일과 폴더의 표 하나와, 각 항목이 커지지 않게 하는 규칙을 가집니다. 파일을 작성하는 코드가 작성할 때 그 규칙을 적용하므로, 파일을 만든 코드가 규칙이 더 남기지 않는 것을 삭제합니다. 삭제가 실패해도 작성하는 코드는 멈추지 않습니다. 실패는 `~/.ghost-alice/state-retention-failures.jsonl`에 한 줄로 기록되고, 이 파일은 마지막 500줄만 남깁니다. `--doctor`는 이 기록을 `state-retention` 줄로 보고합니다. 테스트 하나가 빈 홈에 설치하고, 등록된 훅을 모두 호출하고, 게이트웨이 요청 하나를 전달하고, 제거합니다. 작성된 파일이나 폴더가 표의 어느 항목과도 맞지 않으면 테스트가 실패합니다.

| Path | Rule | Applied by |
| --- | --- | --- |
| `~/.ghost-alice/session-logs/<host>/<session>/` | 모든 호스트 폴더에서 훅 로그가 14일 넘게 갱신되지 않은 세션 폴더를 오래된 순으로 한 번에 50개까지 삭제 | 새 세션의 첫 훅 로그 행 |
| `~/.ghost-alice/io-trace.jsonl` | 10,000줄을 넘으면 마지막 5,000줄만 남김 | io-trace 행마다 |
| `~/.ghost-alice/governance-events.jsonl` | 5MB를 넘으면 지난 파일 `governance-events.jsonl.1` 하나로 옮김 | governance 이벤트마다 |
| `~/.ghost-alice/install/` | 최근 설치 실행 30회분, 실행마다 로그 하나와 이벤트 기록 하나 | 인스톨러, 새 로그를 열기 전 |
| `~/.ghost-alice/install-state/<host>-events.jsonl` | 5MB를 넘으면 지난 파일 하나로 옮김 | 게시 실패나 은퇴 이벤트마다 |
| `~/.ghost-alice/rollbacks/` | 끝난 게시 중 최근 20개. 끝나지 않은 게시는 복원될 때까지 남김 | 게시마다 |
| `~/.ghost-alice/uninstall-reports/` | 최근 보고서 30개 | 제거 명령, 새 보고서를 작성하기 전 |
| 호스트 폴더의 `settings.json.bak*`, `hooks.json.bak*` | 최근 3개 | 훅 설정을 작성할 때마다 |

한도를 크게 넘은 파일은 최근 부분만 한도만큼 지난 파일로 남깁니다. 설치 기록, 호스트 설정, 설치된 스킬과 런타임은 같은 자리에 덮어씁니다. `~/.ghost-alice/uninstall-backup/<host>/support-state/` 아래의 제거 보존 사본, 병합 대기 사본, 은퇴 백업에는 사용자 변경분이 들어 있을 수 있으므로 프로그램이 삭제하지 않습니다.

## Documentation Responsibility

- README는 프로젝트 소개와 빠른 시작을 담습니다.
- 자세한 설치·업데이트 안내는 [installation guide](../getting-started/installation.md)에 둡니다.
- 기여자용 설치 흐름과 보호 규칙은 [installer architecture](./installer-architecture.md)에 둡니다.
- 정책과 실행 환경별 계약 표는 [docs/ko/policies](https://github.com/AidALL/ghost-alice/tree/main/docs/ko/policies)에 둡니다.
- 장문 철학과 결과를 판단에 다시 반영하는 작업 흐름은 [Wiki](https://github.com/AidALL/ghost-alice/wiki/홈)에 두며 [official-docs/derived](https://github.com/AidALL/ghost-alice/tree/main/official-docs/derived)는 공개 기여자 절차를 담습니다.
