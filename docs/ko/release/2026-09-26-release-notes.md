# Ghost-ALICE OS v0.3.0 릴리스 노트

언어: [English](https://github.com/AidALL/ghost-alice/blob/v0.3.0/docs/release/2026-09-26-release-notes.md) | Korean

날짜: 2026-09-26

이번 릴리스는 각 세션 안에서 교정된 사용자 의도를 보존하고 후속 처리 단계에 전달하며, 훅 전달과 설치 동작을 개선합니다. Ghost-ALICE Autopilot과 공개 릴리스 번호를 `v0.3.0`으로 맞춥니다.

[영문 릴리스 노트](https://github.com/AidALL/ghost-alice/blob/v0.3.0/docs/release/2026-09-26-release-notes.md)가 `v0.3.0` 릴리스 본문의 기준 문서이며, 이 파일은 한국어 안내입니다. GitHub Release 본문과 `CHANGELOG.md`는 같은 릴리스를 설명해야 합니다. 과거 릴리스 노트는 변경하지 않습니다.

## Main Changes

- 원장 스냅샷의 `active_decisions`에는 대체되지 않은 결정의 본문을, `latest_scope`에는 기록된 최신 범위를 보존합니다. 라우팅과 skill-evolution이 교정의 의미를 해석할 문맥을 받을 수 있으며, 이 필드만으로 권한을 부여하거나 기존 제한을 없애지는 않습니다.
- 교정 기록은 사용자가 지적했거나 실제로 관찰한 과거 불일치를 새 지시, 예방적 제한, 이전에 유효했던 결과의 재조회 요청과 구분합니다. 교훈을 다시 읽었다는 이유만으로 교정 발생 횟수를 늘리지 않습니다.
- 범위 판단은 현재 지시와 누적된 제약을 함께 해석합니다. 명확한 현재 지시는 특별한 철회 문구 없이도 지정된 범위의 변경을 허용할 수 있지만, 서로 충돌하는 압축 필드만으로 어떤 제한이 먼저였는지 추정하지 않습니다.
- CLI의 의미 기록 변경은 실제 호스트 세션 식별자와 관찰이 완료된 입력 receipt를 따릅니다. 공유 `current-session.json` 포인터가 바뀌었다는 이유만으로 다른 세션에 기록하지 않습니다. 과거 스냅샷을 명시적으로 선택해 읽기 전용으로 조회하는 기능은 유지합니다.
- 입력 관찰 receipt를 훅 문맥을 통해 모델에 전달합니다. 화면에 표시하는 작업 관리 메시지를 줄여도 계속 실행, 차단, 모델 문맥을 제어하는 JSON 필드는 보존합니다.
- 설치기는 검증된 애드온의 정확한 이벤트·명령 조합을 등록하고, 실행 시 지정한 Python과 일반 탐색 경로를 먼저 확인한 뒤 설치 당시 검증한 Python을 마지막 대안으로 사용합니다. 호스트의 전역 Python이나 `PATH`는 변경하지 않습니다.
- Codex 훅 입력에 세션 ID가 없으면 공유 포인터로 넘어가기 전에 실제 thread identity를 확인하며, 후속 처리에서도 같은 세션을 사용합니다. 수동 보안 판단 기록은 선택된 root·플랫폼·세션을 명시하도록 변경했습니다.
- 현재 입력에 대해 확인된 도구 차단은 gate 파일을 저장하지 못해도 유지됩니다. 판단이 없거나 오래되었거나 허용된 경우에는 이 오류 처리 때문에 새 차단을 만들지 않습니다.
- 게시 전에 실제 CI 워크플로의 검사 목록을 확인하고, 실행 가능한 로컬 대응 검사를 모두 수행하며 실행 환경 차이를 기록합니다. 병합할 때에는 현재 커밋의 CI 통과와 GitHub 리뷰 완료를 각각 확인하고 인라인 지적도 검토합니다. 커밋을 교체하면 새 원격 결과를 확인합니다.
- 영문과 한국어 공개 안내는 같은 동작을 설명합니다. 한국어 README는 존댓말로 정리하고, 버전을 맞춘 두 프로젝트의 릴리스 문서를 연결합니다.

## Coordinated Autopilot Release

권장 조합은 Ghost-ALICE OS `0.3.0`과 Ghost-ALICE Autopilot `0.3.0`입니다. 애드온은 core를 통해 설치하는 별도 패키지로 유지됩니다. 애드온이 선언한 기술적 최소 core 버전은 계속 `0.2.2`이며, 릴리스 번호를 맞추는 것만으로 이 하한이 바뀌지는 않습니다. 최신 core 의도 처리 수정과 애드온 연속 실행 수정을 함께 사용하시려면 같은 `0.3.0` 조합을 사용해 주세요.

[Autopilot 수정](https://github.com/AidALL/ghost-alice-autopilot/pull/18)은 대기 중인 완료 receipt나 계획을 적용하기 전에 선택된 현재 의도를 확인합니다. Stop 어댑터는 명시적인 세션 ID와 절대 의도 원장 경로가 주어진 `agent-runtime` 원장도 처리합니다. 문맥이 없거나 충돌할 때 다른 플랫폼이나 세션의 기록으로 대신 처리하지 않습니다. 유효한 receipt는 한 번만 적용하며, 승인된 목표 안에서 내용을 구체화하는 경우에는 기존 승인을 유지합니다.

이 어댑터 계약이 모델 백엔드, 도구 실행기, 호스트 이벤트 루프까지 설치하지는 않습니다. 해당 기능은 호스트가 제공해야 합니다. 독립 bridge CLI의 대상은 계속 Claude와 Codex이며, 플랫폼 지원 범위는 애드온의 `compatibility-matrix.json`을 따릅니다.

## Verification Evidence And Limits

공개 회귀 검사 소스에서 다음 계약을 확인하실 수 있습니다.

- [원장 회귀 검사](https://github.com/AidALL/ghost-alice/blob/v0.3.0/session-intent-analyzer/scripts/test_session_intent_ledger.py)는 결정·범위 보존과 세션에 연결된 의미 기록을 검사합니다.
- [훅 회귀 검사](https://github.com/AidALL/ghost-alice/blob/v0.3.0/_shared/test_session_intent_analyzer_hook.py)는 입력 관찰, receipt, 세션 선택을 검사합니다.
- [Autopilot 어댑터 회귀 검사](https://github.com/AidALL/ghost-alice-autopilot/blob/v0.3.0/tests/test_privileged_adapter.py)는 현재 의도 확인, 승인 경계, receipt 소비를 검사합니다.

소유자가 제공한 자료를 이용한 기록 상태 재생과 모델 해석 시험도 수행했습니다. 해당 산출물은 이번 릴리스에 공개하지 않으므로 비공개 검증 수치는 이 문서에 싣지 않습니다. 공개 테스트 소스는 회귀 검사 범위를 보여주며, 비공개 실행 결과를 대신하는 근거는 아닙니다.

Claude와 Codex의 설치 경로는 모두 확인했습니다. 이번 의도 해석과 실제 행동 시험은 Codex로 수행했으며, 이 시험을 근거로 새 Claude 모델 추론까지 확인했다고 주장하지 않습니다. 구조 검사는 의미 해석 정확도를 증명하지 않고, 재구성 사례는 모든 과거 대화를 그대로 재생하지 않습니다. 이번 회귀 검사는 2주간의 연속 실행 내구성이나 모든 모델·플랫폼의 호환성을 증명하지도 않습니다. 최초 실패와 평가 제외 내역은 저장소 소유자에게 전달한 근거 자료에 보존했습니다.

구현과 검증 요약은 [core PR 44](https://github.com/AidALL/ghost-alice/pull/44)과 [Autopilot PR 18](https://github.com/AidALL/ghost-alice-autopilot/pull/18)에서 확인하실 수 있습니다. 비공개 원장, 원시 대화, 자격 증명, 호스트 설정 백업은 공개 릴리스에 포함하지 않습니다.

## Updating And Checking The Installation

[설치·업데이트 안내](https://github.com/AidALL/ghost-alice/blob/v0.3.0/docs/ko/getting-started/installation.md)에 따라 core 소스를 갱신하고, 사용하실 플랫폼과 애드온을 다시 설치한 뒤 `--status`와 `--doctor`로 결과를 확인해 주세요. 소스 갱신 중 충돌이 보고되면 다시 시도하기 전에 복구 안내를 확인해 주세요. 업데이트를 강행하려고 로컬 변경을 덮어쓰지 마세요.

제품의 `VERSION`, Git 태그, changelog 절, 릴리스 본문은 모두 `0.3.0`을 가리켜야 합니다. 애드온의 저장소 버전, `addon_version`, 릴리스 태그, 설치 sidecar도 `0.3.0`으로 일치해야 합니다. 내부 훅 버전, 카탈로그 버전, 데이터 스키마 버전은 별도로 관리되며 이번 릴리스에서 번호를 맞추지 않습니다.

## License

프로젝트가 소유한 소스 코드와 문서는 계속 Apache License, Version 2.0으로 제공됩니다. [LICENSE](https://github.com/AidALL/ghost-alice/blob/v0.3.0/LICENSE), [NOTICE](https://github.com/AidALL/ghost-alice/blob/v0.3.0/NOTICE), [THIRD_PARTY_NOTICES.md](https://github.com/AidALL/ghost-alice/blob/v0.3.0/THIRD_PARTY_NOTICES.md)의 기존 적용 범위를 유지합니다. 릴리스 버전을 맞추더라도 공개 라이선스는 변경하지 않습니다.
