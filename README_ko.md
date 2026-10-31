# Ghost-ALICE OS

Language: [🇺🇸 English](./README.md) | 🇰🇷 한국어

![Ghost-ALICE OS logo](imgs/Ghost-ALICE_logo.png)

Ghost-ALICE OS는 AI 에이전트의 작업을 관리하는 거버넌스 계층입니다. 지원하는 에이전트 실행 환경에서 사용자의 의도, 작업 범위, 검증 근거, 실행 상태를 확인할 수 있도록 관리합니다.

기존 에이전트 실행 환경 위에서 동작하며, 에이전트가 완료를 선언하기 전에 작업 과정과 근거를 검토할 수 있도록 돕습니다. 프롬프트 모음이나 챗봇 연결 도구, 독립 실행형 에이전트 런타임을 제공하는 저장소는 아닙니다.

## 버전 0.5.1

[웹사이트](https://aidall.github.io/ghost-alice/index_ko.html) · [아키텍처](./architecture_ko.md)

Core 0.5.1은 현재 response-route와 완료 계약을 사용합니다. Apache-2.0으로 제공됩니다.

## 승인된 작업의 진행과 복구

응답 모델은 사용자의 목적과 제약을 유지하며 승인된 작업을 수행하고, 반대 근거가 필수 결과나 의존 관계에 영향을 주는지 판단합니다. 필요한 작업을 수정하면서 지원되는 결과와 미해결 요구를 보존합니다.

단독 흐름은 [Core 아키텍처](./architecture_ko.md), 상세 계약은 [세션 게이트 매트릭스](./docs/ko/policies/session-gate-matrix.md)를 참고해 주세요.

## 빠른 시작

복제한 Ghost-ALICE 저장소 폴더에서 Core를 설치해 주세요. 인스톨러가 사용 가능한 에이전트 플랫폼을 자동으로 감지합니다:

```bash
bash install.sh
```

저장소 복제, 운영체제별 실행 명령, 플랫폼 선택, 업데이트와 문제 해결은 [상세 설치 안내](./docs/ko/getting-started/installation.md)를 확인해 주세요.

## 애드온

애드온은 자체 저장소나 로컬 경로에서 설치합니다:

```bash
bash install.sh --addon-source <저장소-또는-경로>
```

운영체제별 명령, 사용자 정의 애드온, 플랫폼 선택과 제거는 [설치 안내](./docs/ko/getting-started/installation.md)와 [애드온 참조](./docs/ko/reference/official-addons.md)를 확인해 주세요.

## 문서 안내

| 필요한 정보 | 안내 문서 |
| --- | --- |
| 전체 설치·업데이트 명령 | [설치와 업데이트](./docs/ko/getting-started/installation.md) |
| 제거 범위와 정리 방식 | [제거와 정리](./docs/ko/getting-started/uninstall.md) |
| 업데이트 실패, 병합 충돌, 재설치 복구 | [문제 해결](./docs/ko/getting-started/troubleshooting.md) |
| 저장소 구조 | [저장소 구조](./docs/ko/reference/repository-structure.md) |
| 스킬 목록과 참조 정보 | [스킬 카탈로그](./docs/ko/reference/skills.md) |
| 세션 처리 단계와 필수 검사 규칙 | [세션 게이트 매트릭스](./docs/ko/policies/session-gate-matrix.md) |
| 인스톨러의 플랫폼별 호환성 기준 | [인스톨러 플랫폼 호환성](./docs/ko/policies/installer-platform-compatibility-matrix.md) |
| 설계 원칙과 아키텍처 | [GitHub 위키](https://github.com/AidALL/ghost-alice/wiki/홈) |
| 공식 애드온 사용법 | [애드온 참조](./docs/ko/reference/official-addons.md) |
| 애드온 제작 방법 | [위키: 애드온 작성](https://github.com/AidALL/ghost-alice/wiki/애드온-작성) |

## 프로젝트가 지키는 기준

- Ghost-ALICE OS는 에이전트 세션의 작업을 관리하는 운영 계층이며, 범용 운영체제는 아닙니다.
- 사용자의 의도, 작업 범위, 검증 기준, 설치 상태를 핵심 관리 대상으로 다룹니다.
- 인스톨러는 자신이 관리하는 파일과 설정을 추적하며, 관리 대상임을 입증할 수 없으면 삭제 범위를 넓히지 않습니다.

작업 관리 메시지의 표시 수준은 다음 명령으로 조절하실 수 있습니다.

- Claude Code에서는 작업 공간 명령인 `/visibility strict|dynamic|minimal`을 사용합니다.
- Codex를 포함한 모든 플랫폼에서 `_shared/agent_visibility_cli.py`를 통해 같은 프로필 값을 확인하고 변경하실 수 있습니다. Codex에는 `/visibility` 명령이 없습니다.
- 설치 시 기본값은 `dynamic`입니다. 초기 프로필은 `bash install.sh --visibility dynamic` 또는 `.\install.cmd --visibility dynamic`으로 설정하실 수 있습니다. `--agent-visibility`도 호환성을 위한 별칭으로 유지됩니다.
- 표시 수준은 사용자에게 보이는 작업 관리 메시지만 바꿉니다. 훅 실행, 엄격한 감사 로그, 작업 영향 분류인 Work-Impact Projection은 축소하지 않습니다.
- Core가 입력 출처를 확인하지 못했을 때 나오는 Ghost-ALICE 입력 경고는 어느 프로필에서나 같은 세션·같은 원인으로 한 번 표시됩니다. 프로필은 그 경고를 싣는 훅 출력만 바꿉니다. [문제 해결](docs/ko/getting-started/troubleshooting.md#intake-warning-and-doctor-lists)을 참고해 주세요.

전체 제거가 필요하시면 다음 명령을 사용해 주세요.

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

## 기여와 검증

스킬, 인스톨러 동작, 공개 명령을 변경하기 전에는 [AGENTS.md](https://github.com/AidALL/ghost-alice/blob/main/AGENTS.md)를 읽어 주세요. 새 스킬을 만들거나 기존 스킬을 수정하신 뒤에는 [official-docs/derived/skill-compliance-checklist.md](https://github.com/AidALL/ghost-alice/blob/main/official-docs/derived/skill-compliance-checklist.md)의 Phase 1-5를 통과해야 합니다.

공개 문서와 명령 안내의 일관성은 다음 명령으로 검증하실 수 있습니다.

```bash
python3 scripts/validate_public_surfaces.py
```

세션 검사 규칙 검증 명령은 저장소의 `skill-catalog/session-gates.json`과 사용자용 정책 안내를 함께 확인합니다.

```bash
python scripts/check_skill_gate_contract.py
```

인스톨러 호환성 검사 그룹은 다음 명령으로 조회하고 실행하실 수 있습니다.

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
```

## 라이선스

Ghost-ALICE OS 프로젝트가 소유한 소스 코드와 문서는 Apache License, Version 2.0으로 제공됩니다. 자세한 내용은 [LICENSE](./LICENSE), [NOTICE](./NOTICE), [THIRD_PARTY_NOTICES.md](https://github.com/AidALL/ghost-alice/blob/main/THIRD_PARTY_NOTICES.md)를 확인해 주세요.
