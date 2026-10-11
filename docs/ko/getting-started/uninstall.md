# Uninstall Cleanup Procedure

언어: [English](../../getting-started/uninstall.md) | Korean

제거 명령은 설치기가 작성한 `install-state` 매니페스트를 읽습니다. 설치기가 설치하거나 변경했다고 기록한 항목만 제거하거나 복원합니다.

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
| 공식 애드온 하나 제거 | 해당 애드온 저장소의 안내를 따릅니다 | 애드온 패키지의 훅·부속 상태 정리 절차를 따릅니다 |

개별 애드온 제거와 선택한 스킬 제거는 범위가 다릅니다. 전체 제거 명령은 설치된 애드온의 부속 상태를 포함한 Ghost-ALICE 관리 대상을 정리합니다. 공식 애드온 하나만 제거하실 때는 해당 애드온 저장소의 훅·부속 상태 정리 절차를 따라 주세요.

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

설치기는 플랫폼별 매니페스트를 다음 위치에 기록합니다.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

제거 명령은 매니페스트의 `targets`와 `system_env_changes`를 읽습니다. 매니페스트가 없거나 손상되었다면 삭제 범위를 추측하여 넓히지 않고 수동 검토가 필요하다고 보고합니다.

## Cleanup Order

제거 명령은 선택한 제거 범위에 대해 다음 순서로 정리합니다.

1. 플랫폼 훅 설정을 제거합니다.
2. Codex 부트스트랩 또는 전역 규칙의 관리 블록을 제거합니다.
3. Ghost-ALICE가 설정한 경우에만 소스 저장소의 `core.hooksPath`를 복원합니다.
4. 허용된 루트 안에 있고 Ghost-ALICE 소유권 표시나 해시로 검증된 매니페스트 대상만 제거합니다.
5. 설치 상태 매니페스트·이벤트, Codex 훅 기능 복원 메타데이터, 병합 대기 목록, 설치된 훅 실행 파일과 설치 복원 상태를 제거합니다.
6. `~/.ghost-alice/uninstall-reports/` 아래에 제거 결과를 기록합니다.

제거 명령은 결과 보고서 디렉터리와 `~/.ghost-alice/secrets.env` 같은 개인정보 관련 로컬 파일을 보존합니다. `~/.ghost-alice/io-trace.jsonl`은 설치 대상이 아닌 로컬 감사 기록으로 취급합니다.

## Recovery Principles

- 제거 명령은 기록되지 않은 사용자 설정의 복원 방법을 추측하지 않습니다.
- 소유권 표시가 없는 개인 스킬이나 사용자 파일을 자동 삭제하지 않습니다.
- 다른 플랫폼의 매니페스트가 같은 대상을 참조하면 파일을 유지하고 `manual-review`로 표시합니다.
- 없는 파일과 이미 정리된 설정은 실패로 처리하지 않고 `missing` 또는 `unchanged`로 보고합니다.

## system_env_changes

`system_env_changes`는 설치기가 적용한 환경 변경 목록입니다. 제거 명령은 항목에 `before`/`after` 값이나 복원 메타데이터가 있을 때만 해당 변경을 복원합니다.

설치기는 `source_repo_hook_path`, `codex_hooks_feature_flag`, `codex_project_trust` 변경을 기록합니다. 각 종류에 해당하는 제거 처리기와 기록된 복원 조건이 있습니다.

개발자는 새 `kind`를 추가하기 전에 `installer_update/install_state_schema.md`를 갱신하고 설치 기록과 제거 복원 테스트를 모두 추가합니다.
