# Platform Adapter Compliance

언어: [🇺🇸 English](../../policies/platform-adapter-compliance.md) | 🇰🇷 한국어

플랫폼 어댑터 관리자는 이 계약에 따라 Claude, Codex와 terminal-only 어댑터가 실제로 제공하는 기능과 검증 상태를 기록합니다.

## Contents

- [Principles](#principles)
- [Adapter Records](#adapter-records)
- [States](#states)
- [Required Adapters](#required-adapters)
- [Verification](#verification)


## Principles

- 관리자는 실행 환경 점검으로 훅 동작을 검증하지 않은 플랫폼을 `instruction-backed` 상태로 기록합니다.
- `instruction-backed`는 정책 지침, 스킬 배치와 설치 진입점이 있다는 뜻입니다. 네이티브 훅과 같은 실행 동작을 입증한 상태는 아닙니다.
- 설치기가 Codex 훅 파일을 작성해도, 관리자는 실제 이벤트 동작의 점검 근거가 있어야 그 동작을 검사 절차의 완료 근거로 사용할 수 있습니다.
- `terminal-only`에서는 사람이 훅 없는 대체 절차를 따릅니다.

## Adapter Records

관리자는 `skill-catalog/platform-adapters.json`의 각 기록에 다음 필드를 작성합니다.

- `id`
- `state`
- `supported_assets`
- `unsupported_surfaces`
- `install_or_onramp`
- `verification_commands`
- `risk_notes`
- `last_verified_at`
- `owner`
- `source_docs`

## States

- `native`: 플랫폼 실행 환경이 설치기가 관리하는 훅과 스킬 실행 방식을 직접 지원합니다.
- `instruction-backed`: 지침, 스킬 배치와 어댑터 설정은 있지만 실제 훅·이벤트 동작의 점검 근거가 더 필요합니다.
- `terminal-only`: 사람이 프로젝트 정책과 스킬 파일의 대체 절차를 따라야 합니다.

## Required Adapters

- `claude`
- `codex`
- `terminal-only`

## Verification

```bash
python3 scripts/validate_platform_adapters.py
```

검증 명령은 필수 필드·어댑터 ID·날짜 형식·출처 문서의 존재, Codex native 주장의 부재, 훅 근거·대체 절차 기록을 검사합니다.
