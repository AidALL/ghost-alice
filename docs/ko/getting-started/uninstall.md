# Uninstall Cleanup Procedure

언어: [🇺🇸 English](../../getting-started/uninstall.md) | 🇰🇷 한국어

Ghost-ALICE uninstall은 installer가 작성한 `install-state` manifest를 기준으로 동작한다. 목표는 전체 system을 추측으로 복원하는 게 아니다. uninstall은 Ghost-ALICE installer가 installed 또는 changed로 기록한 entry만 제거하거나 복원한다.
## Contents

- [Basic Commands](#basic-commands)
- [Source Of Truth Files](#source-of-truth-files)
- [Cleanup Order](#cleanup-order)
- [Recovery Principles](#recovery-principles)
- [system_env_changes](#systemenvchanges)


## Basic Commands

platform argument가 없는 uninstall은 full uninstall이다.

```bash
bash install.sh --uninstall
```

```powershell
.\install.ps1 -Uninstall
```

```cmd
install.cmd -Uninstall
```

platform을 지정하면 그 platform만 정리한다.

```bash
bash install.sh --platform codex --uninstall
```

```powershell
.\install.ps1 -Platform codex -Uninstall
```

특정 skill만 제거하려면 platform과 skill name을 함께 준다. 다른 Ghost-ALICE skill이 남아 있으면 hooks와 `_shared`는 설치된 채로 둔다.

```bash
bash install.sh --platform codex --uninstall task-router
```

```powershell
.\install.ps1 -Platform codex -Uninstall -Skills task-router
```

## Source Of Truth Files

per-platform manifest는 다음 위치에 있다.

- `~/.ghost-alice/install-state/claude.json`
- `~/.ghost-alice/install-state/codex.json`

Uninstall은 이 manifest의 `targets`와 `system_env_changes`를 읽는다. manifest가 없거나 깨져 있으면 추측 삭제로 범위를 넓히지 않고 manual review 대상으로 보고한다.

## Cleanup Order

1. platform hook settings를 제거한다.
2. Codex bootstrap 또는 global-rule managed block을 제거한다.
3. Ghost-ALICE가 설정한 경우에만 source repository의 `core.hooksPath`를 복원한다.
4. allowed roots 안에 있고 Ghost-ALICE ownership marker 또는 hash로 검증된 manifest target만 제거한다.
5. install-state manifest, install-state event, Codex hook feature rollback metadata, pending merge queue, 설치된 hook dispatcher asset, install rollback 같은 support state를 제거한다.
6. `~/.ghost-alice/uninstall-reports/` 아래에 report를 작성한다.

uninstall report 디렉터리와 `~/.ghost-alice/secrets.env` 같은 privacy-sensitive local 파일은 별도의 explicit purge mode가 생기기 전까지 보존한다. `~/.ghost-alice/io-trace.jsonl` 같은 runtime audit trace는 install target이 아니라 local audit record로 취급한다.

## Recovery Principles

- 기록되지 않은 user settings를 어떻게 복원할지 추측하지 않는다.
- marker 없는 personal skill이나 user-owned 파일을 자동 삭제하지 않는다.
- 다른 platform manifest가 같은 target을 참조하면 그대로 두고 `manual-review`로 표시한다.
- 없는 파일과 이미 정리된 settings는 failure가 아니라 `missing` 또는 `unchanged`로 보고한다.

## system_env_changes

`system_env_changes`는 installer가 적용한 environment 변경 목록이다. uninstall은 entry에 `before`/`after` 값이나 rollback metadata가 있을 때만 rollback한다.

현재 구현은 `source_repo_hook_path`와 `codex_hooks_feature_flag`를 emit한다. 다른 `kind` 값은 reserved schema slot이고, install-recording과 uninstall-rollback test가 생기기 전까지는 실제 rollback 대상이 아니다.

- current: `source_repo_hook_path`
- current: `codex_hooks_feature_flag`
- reserved: `ps_policy_change`, `posix_rc_change`, `macos_quarantine_fix`, `posix_chmod_fix`

새 `kind`를 추가할 때는 먼저 `installer_update/install_state_schema.md`를 갱신하고 install-recording과 uninstall-rollback test를 모두 추가한다.
