# Uninstall Cleanup Procedure

언어: [English](../../getting-started/uninstall.md) | Korean

Ghost-ALICE uninstall은 installer가 작성한 `install-state` manifest를 기준으로 동작합니다. uninstall path는 Ghost-ALICE installer가 installed 또는 changed로 기록한 entry만 제거하거나 복원합니다.

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
| managed Ghost-ALICE surface 전체 제거 | `bash install.sh --uninstall` | recorded target 전체에 대한 full uninstall |
| 한 platform 제거 | `bash install.sh --platform codex --uninstall` | 다른 platform install은 유지 |
| 선택한 skill 제거 | `bash install.sh --platform codex --uninstall task-router` | 다른 skill이 남으면 hooks와 shared assets 유지 |
| official addon 하나 제거 | 해당 addon repository documentation을 따릅니다 | addon hook과 sidecar cleanup을 addon package와 정렬 |

Addon-specific removal은 selected skill removal과 다릅니다. Full uninstall은 installed addon sidecar cleanup을 포함해 managed Ghost-ALICE footprint를 제거합니다. official addon 하나만 제거하려면 해당 addon repository documentation을 따라 hook과 sidecar cleanup이 addon package와 정렬되게 합니다.

## Basic Commands

platform argument가 없는 uninstall은 full uninstall입니다.

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

platform을 지정하면 그 platform만 정리합니다.

```bash
bash install.sh --platform codex --uninstall
```

```cmd
.\install.cmd --platform codex --uninstall
```

특정 skill만 제거하려면 platform과 skill name을 함께 줍니다. 다른 Ghost-ALICE skill이 남아 있으면 hooks와 `_shared`는 설치된 채로 둡니다.

```bash
bash install.sh --platform codex --uninstall task-router
```

```cmd
.\install.cmd --platform codex --uninstall task-router
```

## Source Of Truth Files

per-platform manifest는 다음 위치에 있습니다.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

Uninstall은 이 manifest의 `targets`와 `system_env_changes`를 읽습니다. manifest가 없거나 깨져 있으면 추측 삭제로 범위를 넓히지 않고 manual review 대상으로 보고합니다.

## Cleanup Order

1. platform hook settings를 제거합니다.
2. Codex bootstrap 또는 global-rule managed block을 제거합니다.
3. Ghost-ALICE가 설정한 경우에만 source repository의 `core.hooksPath`를 복원합니다.
4. allowed roots 안에 있고 Ghost-ALICE ownership marker 또는 hash로 검증된 manifest target만 제거합니다.
5. install-state manifest, install-state event, Codex hook feature rollback metadata, pending merge queue, 설치된 hook dispatcher asset, install rollback 같은 support state를 제거합니다.
6. `~/.ghost-alice/uninstall-reports/` 아래에 report를 작성합니다.

uninstall report 디렉터리와 `~/.ghost-alice/secrets.env` 같은 privacy-sensitive local 파일은 별도의 explicit purge mode가 생기기 전까지 보존합니다. `~/.ghost-alice/io-trace.jsonl` 같은 runtime audit trace는 install target이 아니라 local audit record로 취급합니다.

## Recovery Principles

- 기록되지 않은 user settings를 어떻게 복원할지 추측하지 않습니다.
- marker 없는 personal skill이나 user-owned 파일을 자동 삭제하지 않습니다.
- 다른 platform manifest가 같은 target을 참조하면 그대로 두고 `manual-review`로 표시합니다.
- 없는 파일과 이미 정리된 settings는 failure가 아니라 `missing` 또는 `unchanged`로 보고합니다.

## system_env_changes

`system_env_changes`는 installer가 적용한 environment 변경 목록입니다. uninstall은 entry에 `before`/`after` 값이나 rollback metadata가 있을 때만 rollback합니다.

현재 구현은 `source_repo_hook_path`, `codex_hooks_feature_flag`, `codex_project_trust`를 emit합니다. 다른 `kind` 값은 reserved schema slot이고, install-recording과 uninstall-rollback test가 생기기 전까지는 실제 rollback 대상이 아닙니다.

- current: `source_repo_hook_path`
- current: `codex_hooks_feature_flag`
- current: `codex_project_trust`
- reserved: `ps_policy_change`, `posix_rc_change`, `macos_quarantine_fix`, `posix_chmod_fix`

새 `kind`를 추가할 때는 먼저 `installer_update/install_state_schema.md`를 갱신하고 install-recording과 uninstall-rollback test를 모두 추가합니다.
