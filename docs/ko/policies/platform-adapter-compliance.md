# 플랫폼 어댑터 준수

[🇺🇸 English](../../policies/platform-adapter-compliance.md) | [🇰🇷 한국어](./platform-adapter-compliance.md)

이 계약에 따라 `skill-catalog/platform-adapters.json`은 Claude, Codex와 `terminal-only` 어댑터가 실제로 지원하는 기능과 그 지원의 검증 상태를 과장 없이 기록합니다.

## 목차

- [원칙](#원칙)
- [어댑터 기록](#어댑터-기록)
- [상태](#상태)
- [필수 어댑터](#필수-어댑터)
- [검증](#검증)


## 원칙

- 관리자는 실환경 점검으로 훅 동작을 검증하지 않은 플랫폼을 `instruction-backed` 상태로 기록합니다.
- 인스톨러가 Codex 훅 파일을 작성해도, 그 이벤트 동작은 실환경 점검 근거가 있어야 게이트 완료 근거로 사용할 수 있습니다.

## 어댑터 기록

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

## 상태

- `native`: 플랫폼 실행 환경이 인스톨러가 관리하는 훅과 스킬 표면을 직접 지원합니다.
- `instruction-backed`: 지침, 스킬 배치와 설치 진입점은 있지만 실제 훅·이벤트 동작의 점검 근거가 더 필요합니다.
- `terminal-only`: 사람이 대체 절차로 프로젝트 정책과 스킬 파일을 따라야 합니다.

## 필수 어댑터

- `claude`
- `codex`
- `terminal-only`

## 검증

```bash
python3 scripts/validate_platform_adapters.py
```

검증 명령은 필수 필드와 어댑터 ID, 날짜 형식, 출처 문서의 존재를 검사합니다. Codex의 `native` 상태를 거부하고, Codex의 `unsupported_surfaces`와 `risk_notes`의 글이 `hook`과 `evidence`·`runtime`·`smoke` 가운데 하나를 포함하는지, `event`와 `firing proof`를 포함하는지 검사합니다. Claude의 `supported_assets`가 `repo-local`과 `installed-global`을 모두 포함하는지도 검사합니다.
