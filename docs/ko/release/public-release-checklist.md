# Public Release Checklist

Language: [🇺🇸 English](../../release/public-release-checklist.md) | 🇰🇷 한국어

배포 담당자는 이 점검표로 현재 Ghost-ALICE Core 저장소와 공개 문서를 배포합니다. 기존 저장소 이력을 유지하고 승인된 배포 범위만 준비합니다. 이 문서는 배포 요건을 정의하며 특정 후보의 통과를 증명하지 않습니다.

## Contents

- [Release Scope](#release-scope)
- [Documentation And Public Surfaces](#documentation-and-public-surfaces)
- [Source Verification](#source-verification)
- [Installed Behavior Gates](#installed-behavior-gates)
- [License And Private Material](#license-and-private-material)
- [Publication](#publication)

## Release Scope

- 배포 담당자는 후보 commit을 정하고 이번 배포에 속하는 검토된 Core·인스톨러·공개 스킬·스키마·테스트·문서를 포함합니다. 무관한 로컬 변경은 보존합니다.
- `VERSION`, 애드온 요구 조건과 현재 호환성 기록을 일치시킵니다. Core와 Ghost-ALICE Autopilot은 별도 패키지이며 Core 소스 업데이트만으로 Ghost-ALICE Autopilot이 게시되거나 설치되지는 않습니다.
- `.tmp/`, 로컬 worktree, 설치 보고서, 세션 기록, 자격 증명, 비공개 애드온, 조직별 자료와 생성 캐시는 공개 산출물에서 제외합니다. 홈 디렉터리의 설치 사본이나 로컬 에이전트 설정도 포함하지 않습니다.
- 소스 준비, 로컬 설치, 실행 중인 호스트의 반영, 통합과 공개 게시를 구별합니다. 한 단계의 근거로 다른 단계까지 입증하지 않습니다.

## Documentation And Public Surfaces

- 같은 배포 범위에서 `README.md`/`README_ko.md`, 아키텍처, `docs/`/`docs/ko/` 대응 문서, Wiki와 영문·국문 GitHub Pages 진입점을 맞춥니다.
- 문서·Wiki·Pages에는 현재의 책임, 명령과 한계를 설명합니다. 무엇을 어떻게 바꿨는지는 commit 메시지에만 기록하며, 운영 문서에는 변경 이력·전후 비교·업그레이드 서술을 넣지 않습니다.
- 언어가 달라도 명령·경로·훅·스키마·열거 토큰은 유지합니다. 대응 문서 링크와 생성되는 공개 안내를 담당 카탈로그와 대조합니다.
- 구현 계약의 기준은 저장소 문서입니다. Wiki는 양 언어 설명·복구 안내를, Pages는 공개 진입점을 제공합니다. 현재 의미가 일치하도록 게시합니다.
- `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, 행동 강령과 이슈·PR 양식을 실제 정책과 일치시킵니다.

## Source Verification

배포 담당자는 후보가 영향을 주는 계약으로 검증 계획을 구성합니다. 변경 없는 유효한 근거는 원래 출처와 검증 시각으로 재사용합니다. 새 상태 질문, 문서 편집이나 기록 복구 때문에 이미 검증한 업무를 반복하지 않습니다. 필수 검증이 없으면 해당 기준은 미해결로 남습니다.

사용 가능한 소스 검사는 다음과 같습니다.

```bash
python3 scripts/validate_public_surfaces.py
python3 scripts/check_skill_gate_contract.py
python3 scripts/check_prose_wrapping.py
python3 scripts/validate_platform_adapters.py
python3 scripts/run_installer_compat_tests.py --list
```

인스톨러 동작에 영향이 있으면 해당 호환성 그룹을 사용합니다. 공용 실행 코드나 스크립트를 바꾸는 배포에는 `shared-all`과 `scripts-all`의 관련 검증이 필요하며, 변경이 여러 호환성 계약에 걸치면 전체 호환성 검사를 사용합니다. 담당 테스트는 [인스톨러 호환성 계약](../policies/installer-platform-compatibility-matrix.md)을 확인합니다. 정적 검사 통과는 실제 네이티브 호출이나 모델 행동의 근거가 아닙니다.

## Installed Behavior Gates

설치된 실행 환경을 주장하려면 후보 설치의 출처와 [실행 점검 절차](../policies/live-smoke-regression.md)의 실제 네이티브 훅 관측을 보존합니다. 디스크의 훅 설정, 네이티브 신뢰와 실행 중인 호스트의 반영은 별개입니다. 필요하면 [재로드 복구 안내](../getting-started/troubleshooting.md#native-hook-trust-and-reload)를 따릅니다.

거버넌스 점검은 실행 연결과 제어 표시를 입증하며 별도의 비공개 행동 검사를 대신하지 않습니다. 정규 인스톨러로 후보를 설치하고 인스톨러 소유 매니페스트를 갖춘 임시 환경에서, 지원을 주장하는 각 설치 플랫폼에 새 세션 평가 컨트롤러를 사용합니다. 자리표시자를 배포 담당자의 사례·평가 도구 경로로 바꾸며 평가 전용 비공개 자료는 대상 명령과 환경에 전달하지 않습니다.

```bash
python3 -m _shared.blind_behavior --case <sealed-held-out-case.json> --platform claude --subject-command '["claude"]' --evaluator-command '["<trusted-evaluator>"]' --record .tmp/release/blind-claude.json
python3 -m _shared.blind_behavior --case <sealed-held-out-case.json> --platform codex --subject-command '["codex"]' --evaluator-command '["<trusted-evaluator>"]' --record .tmp/release/blind-codex.json
```

필수 기록에는 종료 코드 0, `verdict=pass`, 의도한 설치 플랫폼 출처와 컨트롤러가 계산한 suite digest가 있어야 합니다. 요청 원문, rubric, 정답, 대상 응답과 평가 전용 비공개 문구는 저장하지 않습니다. 한 플랫폼의 통과는 다른 플랫폼의 근거가 아닙니다. 자동 비공개 평가는 사람이 깨끗한 새 터미널에서 수행하는 수락 검사를 대신하지 않습니다. 변경 없는 유효한 근거는 유지할 수 있으며, 없거나 변경의 영향을 받은 근거는 해당 배포 주장을 하기 전에 확보합니다.

## License And Private Material

- `LICENSE`가 수정되지 않은 Apache License, Version 2.0 원문인지 확인하고 두 README가 `NOTICE`·`THIRD_PARTY_NOTICES.md`의 라이선스 범위와 일치하는지 확인합니다.
- 포함된 제삼자 자료에는 출처 URL·라이선스·저작권 고지·로컬 경로가 필요합니다. 상위 문서 사본 대신 짧은 인용이 있는 프로젝트 고유 설명을 유지합니다.
- 관리자가 시행하지 않는 CLA, DCO, 소유권 규칙이나 승인 절차를 암시하지 않습니다. `CODEOWNERS`가 있으면 필요한 접근 권한을 갖춘 실제 사용자·팀을 지정합니다.
- 취약점 보고는 공개 이슈 대신 `SECURITY.md` 또는 비공개 취약점 보고 경로로 안내합니다.
- 후보 산출물에 비밀 값이나 비공개 자료가 포함되었는지 검토합니다. 정책이나 자격 증명 도우미 예시의 검색 결과는 문맥을 확인하며 키워드만으로 유출을 판단하지 않습니다.

## Publication

1. 기존 브랜치·검토 절차를 통해 검토된 배포 범위만 통합합니다. 배포를 위해 저장소를 새로 초기화하거나 이력을 재작성하지 않습니다.
2. 필수 소스·설치·행동 근거가 후보에 적용되는지 확인하고 지원 근거가 없는 플랫폼이나 범위를 명시합니다.
3. 승인된 commit·버전 tag·배포 산출물을 게시합니다. 배포와 pull request 설명에는 현재 패키지 동작·설치·검증·한계를 담으며 변경 이력은 commit 메시지에만 기록합니다.
4. 검토된 소스에서 양 언어 Wiki와 GitHub Pages를 게시하고 공개된 페이지가 의도한 후보와 일치하는지 확인합니다.
5. 실제 CI와 게시 결과를 확인합니다. 로컬 commit, 대기 중인 배포나 접수된 업로드 요청은 공개 게시의 증거가 아닙니다.
6. 배포·설치 경로가 바뀌었다면 임시 계정이나 컴퓨터의 새 복제본에서 영향받은 공개 설치 경로를 확인합니다. 무관한 개발 설치본은 보존합니다.
