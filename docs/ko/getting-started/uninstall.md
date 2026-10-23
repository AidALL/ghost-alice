# Uninstall Cleanup Procedure

Language: [🇺🇸 English](../../getting-started/uninstall.md) | 🇰🇷 한국어

제거 명령은 인스톨러가 작성한 `install-state` 매니페스트를 읽습니다. 인스톨러가 설치하거나 변경했다고 기록한 항목만 제거하거나 복원합니다.

## Contents

- [Choose The Removal Scope](#choose-the-removal-scope)
- [Basic Commands](#basic-commands)
- [Source Of Truth Files](#source-of-truth-files)
- [Cleanup Order](#cleanup-order)
- [Recovery Principles](#recovery-principles)
- [system_env_changes](#system_env_changes)

## Choose The Removal Scope

| Goal | Command path | Notes |
| --- | --- | --- |
| Ghost-ALICE 관리 대상 전체 제거 | `bash install.sh --uninstall` | 기록된 대상 전체를 제거합니다 |
| 한 플랫폼 제거 | `bash install.sh --platform codex --uninstall` | 다른 플랫폼의 설치는 유지합니다 |
| 선택한 스킬 제거 | `bash install.sh --platform codex --uninstall task-router` | 다른 스킬이 남으면 훅과 공유 파일을 유지합니다 |
| 애드온 하나 제거, Core 유지 | `bash install.sh --platform codex --uninstall --addon <애드온-id>` | 애드온의 훅을 먼저 해제하고 검증된 전용 파일을 제거합니다 |

개별 애드온 제거와 선택한 스킬 제거는 범위가 다릅니다. Core 갱신은 기존 애드온을 유지하며, 명시적인 애드온 제거 명령이 제거 범위를 선택합니다. 전체 제거는 설치된 애드온의 부속 기록을 포함한 활성 관리 대상을 정리하면서 원래 복구 사본과 로컬 근거는 보존합니다. 개별 애드온을 제거하면 Core와 다른 애드온의 설치는 유지합니다. 선택한 스킬을 제거할 때는 기존 의존성 확인이 설치된 애드온에 필요한 Core 스킬을 보호합니다.

## Basic Commands

플랫폼을 지정하지 않으면 제거 명령이 관리 대상 전체를 정리합니다.

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

플랫폼을 지정하면 제거 명령이 해당 플랫폼만 정리합니다.

```bash
bash install.sh --platform codex --uninstall
```

```cmd
.\install.cmd --platform codex --uninstall
```

특정 스킬만 제거하려면 플랫폼과 스킬 이름을 함께 지정해 주세요. 다른 Ghost-ALICE 스킬이 남아 있으면 제거 명령은 훅과 `_shared`를 유지합니다.

```bash
bash install.sh --platform codex --uninstall task-router
```

```cmd
.\install.cmd --platform codex --uninstall task-router
```

## Source Of Truth Files

인스톨러는 플랫폼별 매니페스트를 다음 위치에 기록합니다.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

제거 명령은 매니페스트의 `targets`와 `system_env_changes`를 읽습니다. 매니페스트가 없거나 손상되었다면 삭제 범위를 추측하여 넓히지 않고 수동 검토가 필요하다고 보고합니다.

## Cleanup Order

제거 명령은 선택한 제거 범위에 대해 다음 순서로 정리합니다.

1. 전체 또는 플랫폼 제거에서는 해당 플랫폼의 설치된 애드온부터 제거합니다. 제거 표시를 기록하고 정확히 소유한 애드온 훅 등록과 남아 있는 은퇴한 어댑터 등록을 해제한 뒤 검증된 전용 파일을 제거합니다. 훅 기록 변경이 실패하면 파일과 부속 기록을 남깁니다. 수정된 대상과 부속 기록·제거 표시는 검토 또는 재시도를 위해 유지하며 재시도는 해제한 훅을 복구하지 않습니다.
2. 제거 루트가 등록된 컨텍스트 gateway의 런타임(`~/.ghost-alice/runtime/current`, 그 아래 또는 그 위 폴더)이나 서비스 파일 중 하나이거나, 제거가 Codex 전체 제거이면 Core 훅이나 파일을 변경하기 전에 해당 gateway를 분리합니다. 설치 기록의 `service` 섹션이 서비스 파일을 지정합니다. 서비스 중지를 확인한 뒤 소유한 launch agent와 provider 블록을 제거하고 `service` 섹션을 삭제하며, 주변의 현재 사용자 설정, 상태 디렉터리, 구성 요소 로그와 기존 복구 자료는 유지합니다. Codex 구성이나 gateway 서비스·상태 파일 자체가 심볼릭 링크이거나, 서비스 파일이 인스톨러 밖에서 바뀌었거나, 해제 결과를 확인할 수 없으면 뒤따르는 Core 정리를 실패로 중단합니다. Claude 제거나 선택한 Codex 스킬 제거처럼 서비스의 코드를 건드리지 않는 제거는 gateway를 실행 상태로 유지합니다. 링크는 가리키는 곳이 아니라 링크 자신의 경로로 판정합니다. `service` 섹션이 없으면 제거는 launchd에 조회하며, 적재된 gateway 서비스의 프로그램이 제거 루트 안이면 정리를 검토 대상으로 중지합니다.
3. Core 플랫폼 훅 설정과 Codex 부트스트랩 또는 전역 규칙의 관리 블록을 제거하면서 사용자 소유 항목은 유지합니다.
4. 소스 저장소의 공용 `core.hooksPath`는 마지막 호스트의 전체 제거에서만 복원합니다. 지금 값이 install-state가 설치가 작성했다고 기록한 값과 같을 때만, install-state가 기록한 설치 전의 값을 다시 작성합니다(설치 전 값이 없으면 설정을 삭제합니다). 다른 호스트의 install-state가 남아 있으면 그 호스트를 위해 값을 그대로 두고, 기록이 없는 값이나 설치 뒤에 바뀐 값도 그대로 둡니다. 그다음 기록된 기능·신뢰 설정의 복원 조건을 적용합니다.
5. 허용된 루트 안에 있고 소유권 표시나 해시로 검증된 매니페스트 대상만 제거합니다. 다른 플랫폼의 설치 상태 매니페스트가 남아 있으면 공용 런타임과 훅 실행 파일은 유지합니다.
6. 선택한 플랫폼의 미결 병합 원본을 보존한 뒤 활성 대기 목록을 제거합니다. 다른 플랫폼이 설치돼 있으면 공용 설치 복구 사본은 유지하고, 마지막 플랫폼이면 먼저 보존합니다. 각 사본은 `~/.ghost-alice/uninstall-backup/<platform>/support-state/` 아래 새 디렉터리에 기록하며 보고서의 `backup_path`가 그 원본을 가리킵니다. 원래 수정 시각과 심볼릭 링크 표현을 유지하고 기존 사본도 보존합니다. 복구 자료의 루트를 제거하기 전에 최상위·내부 링크 각각의 보존 사본 수정 시각을 수집한 원본 시각과 비교합니다. 링크 시각을 보존할 수 없으면 원본 루트와 설치 상태 매니페스트를 검토 대상으로 남깁니다. Windows junction을 포함하는 복구 자료도 자동 복사·삭제하지 않고 그대로 남깁니다.
7. 지원 파일 정리와 복구 사본 보존이 끝날 때까지 설치 상태 매니페스트를 유지합니다. 나머지 설치 지원 기록을 정리하고 `~/.ghost-alice/uninstall-reports/` 아래에 결과를 기록합니다. 새 보고서를 작성하기 전에 이 폴더는 최근 보고서 30개만 남깁니다.

제거 명령은 보존 사본 디렉터리, 최근 제거 보고서, 소스 작업 트리, 대화·세션 근거, 의도 원장과 `~/.ghost-alice/secrets.env` 같은 개인정보 관련 로컬 파일을 삭제하지 않습니다. 영구 보존한다는 뜻은 아닙니다. Ghost-ALICE가 설치되어 있는 동안에는 [런타임 상태 보존](../reference/repository-structure.md#runtime-state-retention) 규칙이 이 자료에 따로 적용됩니다. 예를 들어 훅 로그가 14일 넘게 갱신되지 않은 세션 폴더를 삭제합니다. 설치 기록 `~/.ghost-alice/installed-payload.json`과 `~/.ghost-alice/rollbacks/` 아래의 배치 폴더는 두 호스트가 함께 씁니다. 한 호스트의 전체 제거는 다른 호스트를 위해 둘을 남기고(`manual-review`, `shared-support-in-use-by-other-platform`), 마지막 호스트의 전체 제거가 배치 폴더를 `~/.ghost-alice/uninstall-backup/<platform>/support-state/`에 보존한 뒤 둘을 제거합니다. `~/.ghost-alice/io-trace.jsonl`은 설치 대상이 아닌 로컬 감사 기록으로 취급합니다. 실행 연결 제거가 자료 전체 삭제를 뜻하지는 않습니다. 이 설정·파일 결과만으로 실행 중인 호스트의 반영이나 이미 전달된 작업의 중지를 확인하지 않습니다. 확인 없는 미리보기는 설치·설정·복구 자료를 변경하지 않지만 기존 CLI처럼 결과 보고서는 기록합니다.

## Recovery Principles

개별 애드온 제거는 파일을 삭제하기 전에 Claude의 인스톨러 추가 스킬 권한과 Codex의 소유한 훅 신뢰 설정을 해제합니다. Claude 권한 소유권은 설정 파일과 매니페스트가 선택한 스킬 이름에 결합됩니다. 기존 권한, 사용자가 추가한 중복 권한과 소유권을 모르는 이전 권한은 보호합니다. Codex는 같은 명령이 반복돼도 각 훅의 실제 그룹·핸들러 위치에 맞춰 남은 신뢰 테이블을 옮기며 사용자 필드와 주석을 유지합니다. 대상 위치의 충돌을 해결할 수 없으면 제거를 중단합니다. 설정 저장은 심볼릭 링크를 유지하고 원자적으로 교체합니다. 이어지는 훅 설정 저장이 실패하면 원래 신뢰 설정의 내용과 수정 시각을 복구합니다. 복구 자체가 실패할 경우에도 원래 설정의 보존 사본을 남깁니다.

애드온 제거 CLI는 오류나 부분 제거에 종료 코드 `2`를 반환하며, 중단된 제거의 재개 오류도 포함합니다. 종료 코드 `1`은 애드온을 찾지 못한 경우입니다. 수정된 대상의 보존, 불명확한 권한 소유권이나 설정 오류를 완전한 제거로 계산하지 않습니다.

- 제거 명령은 기록되지 않은 사용자 설정의 복원 방법을 추측하지 않습니다.
- 소유권 표시가 없는 개인 스킬이나 사용자 파일을 자동 삭제하지 않습니다.
- 다른 플랫폼의 매니페스트가 같은 대상을 참조하면 파일을 유지하고 `manual-review`로 표시합니다.
- 없는 파일과 이미 정리된 설정은 실패로 처리하지 않고 `missing` 또는 `unchanged`로 보고합니다.

## system_env_changes

`system_env_changes`는 인스톨러가 적용한 환경 변경 목록입니다. 제거 명령은 항목에 `before`/`after` 값이나 복원 메타데이터가 있을 때만 해당 변경을 복원합니다.

인스톨러는 `source_repo_hook_path`, `codex_hooks_feature_flag`, `codex_project_trust` 변경을 기록합니다. 각 종류에 해당하는 제거 처리기와 기록된 복원 조건이 있습니다.

개발자는 새 `kind`를 추가하기 전에 `installer_update/install_state_schema.md`를 갱신하고 설치 기록과 제거 복원 테스트를 모두 추가합니다.
