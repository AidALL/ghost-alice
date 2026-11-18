# Autopilot 0.5.1 Windows 검증: 테스트 실패 22건 정리와 셸별 결과

Nov 18, 2026 · kyu

- 기준 버전: `test-baseline-2026-11-16` (Core `c6019ac`, Autopilot `v0.5.1` `2075768`)
- 확인 환경: Windows 11 Pro 10.0.26200 (한국어, cp949), PowerShell 5.1 / PowerShell 7.7.0 / Command Prompt, Python 3.14, Claude Code
- 관련 링크: PR [#2](https://github.com/bogop8-star/ghost-alice/pull/2) (`autopilot/windows-verify` → `autopilot/main`), [발견 사항 상세](https://github.com/bogop8-star/ghost-alice/blob/autopilot/windows-verify/docs/windows-verification/2026-11-18-findings.md), [설계](https://github.com/bogop8-star/ghost-alice/blob/autopilot/windows-verify/docs/coding-convention/specs/2026-11-18-windows-verification-design.md)

## 1. 목표

단점 6번 "설치와 환경 맞추기가 어렵다" 중 Windows 부분을 다룹니다. Autopilot 0.5.1의 자동 테스트가 Windows에서 동작하는지 확인하고, Windows에서만 나는 실패를 "테스트 쪽 문제"와 "실제 동작 차이"로 나눠 정리합니다. 공식 호환표(`compatibility-matrix.json`)에서 Windows 항목이 모두 `not-run` 이던 것을 실제 근거로 채웁니다. AidALL 공식 업데이트는 따라가지 않고 11/12 설치 기준에서만 검증합니다.

## 2. 한 일

- 설치 기준 고정 확인: Core·Autopilot·설치본이 모두 11/12 설치 그대로이고 자동 업데이트 경로가 없음을 확인했습니다. Core 폴더에서 `git pull` 시 자동 재설치되는 Git 훅(`core.hooksPath`)은 꺼 두었습니다.
- 설치본과 분리된 개발 폴더(git worktree, 브랜치 `windows-verify`)에서만 작업했습니다. 어댑터 코드(`addons/`)는 바꾸지 않아 설치본 지문이 그대로입니다.
- 전체 테스트를 Windows에서 실행해 실패 22건의 원인을 하나씩 재현하고 분류했습니다.
- 테스트 쪽 차이 21건을 고쳤고, 실제 동작 차이 1건은 Windows에서 xfail(알려진 실패)로 표시했습니다.
- 테스트 실행기(`scripts/run_windows_tests.ps1`)를 보완했습니다. 파이썬과 pytest가 없으면 이유를 알려 주고 멈춥니다.
- PowerShell 5, PowerShell 7, CMD에서 각각 전체 테스트를 실행했습니다.
- 호환표 Windows 항목 3개에 근거를 추가했습니다.
- Autopilot은 팀 fork가 없어서, 새 fork 대신 이 저장소의 `autopilot/` 브랜치에 올리고 PR을 만들었습니다.
- CI가 AidALL 최신 Core를 받아 첫 단계에서 실패하던 문제를 고쳤습니다. 이제 CI는 팀 태그 `test-baseline-2026-11-16` 의 Core를 받습니다.

## 3. 결과

| 실행 | 통과 | 실패 | 건너뜀 | xfail | 소요 |
| --- | --- | --- | --- | --- | --- |
| 수정 전 (Windows, PowerShell 5) | 740 | 22 | 2 | 0 | 47분 |
| 수정 후 (Windows, PowerShell 5.1) | 759 | 0 | 4 | 1 | 43분 |
| 수정 후 (Windows, PowerShell 7.7.0) | 759 | 0 | 4 | 1 | 46분 |
| 수정 후 (Windows, CMD) | 759 | 0 | 4 | 1 | 63분 |
| 수정 후 (Linux, GitHub CI) | 762 | 0 | 2 | 0 | 7분 |

Linux CI에서는 Windows에서 건너뛰거나 xfail로 둔 테스트까지 모두 통과했습니다. 테스트 수정이 Linux 동작을 바꾸지 않았다는 근거입니다. Linux에서 건너뛴 2건은 macOS 전용 도구(`sandbox-exec`)가 필요한 원래 테스트입니다.

호환표의 Windows 항목은 상태를 `not-run` 으로 유지했습니다. 호환표는 "그 셸에서 install/status/remove 직접 실행"을 요구하는데, 이것은 아직 하지 않았기 때문입니다.

## 4. 발견한 문제

| # | 문제 | 근거 | 영향 | 처리 |
| --- | --- | --- | --- | --- |
| 1 | 실행 폴더 자리에 일반 파일이 있으면 Windows에서는 경고 없이 넘어감 (Autopilot 어댑터) | Windows는 일반 파일 아래 경로를 `FileNotFoundError` 로 보고해, 어댑터가 "실행 기록 없음"으로 처리 | 설정 오류가 사용자에게 보이지 않음 | 기록만 (고치면 설치본 지문이 바뀜). 테스트는 Windows xfail |
| 2 | 줄바꿈이 CRLF인 완료 문구는 기록이 거부될 수 있음 (Autopilot) | 완료 기록이 바이트 단위로 비교함. 테스트에서 재현 | Git Bash 경로는 문제없음. CRLF 파일을 넘길 때 거부될 것으로 추정 | 기록만 |
| 3 | 한국어 Windows에서 Core 응답 검토 도구가 한국어 파일을 읽지 못함 (Core) | `'cp949' codec can't decode` 오류, `PYTHONUTF8=1` 로 우회됨 | 수동 응답 검토 기록 실패. 훅 경로 영향은 미확인 | 기록만 (Core는 이번 범위 밖) |
| 4 | Windows 테스트 실행기가 PowerShell 5에서 바로 멈춤 | stderr 처리와 인자 따옴표 제거 | 팀원이 테스트를 돌리기 어려움 | 수정 |
| 5 | CI가 AidALL 최신 Core를 받아 0.5.1 기준과 맞지 않음 | 손대지 않은 `autopilot/main` 도 같은 단계에서 실패 | CI가 테스트를 실행하지 못함 | 수정 (팀 태그로 고정) |
| 6 | Claude가 Ghost-ALICE 규칙을 자주 빠뜨림 | 이 작업 세션에서 Stop 훅 경고 7번 (`route-unavailable` 2, `criterion-unbound` 5) | 응답 경로·응답 검토 기록 누락 | 기록만. 진행 방식을 바꿔 줄임 |

테스트 쪽 차이 21건(Windows `chmod` 한계, 텍스트 모드 줄바꿈 변환, 경로 따옴표, 절대경로, 한국어 오류 문구 등)의 상세는 [발견 사항 문서](https://github.com/bogop8-star/ghost-alice/blob/autopilot/windows-verify/docs/windows-verification/2026-11-18-findings.md) 3절에 있습니다.

## 5. 확인하지 못한 것

- 각 셸에서 install/status/remove를 직접 실행 (호환표가 요구하는 항목)
- 실제 Claude Code 세션에서 Windows 훅 전체 흐름. 테스트는 부품 단위 확인입니다.
- 발견 사항 2번의 CRLF 파일 사례 직접 재현, 3번의 훅 경로 영향
- 다른 팀원 PC에서의 실행 결과

## 6. 다음 단계

- PR [#2](https://github.com/bogop8-star/ghost-alice/pull/2) 검토 (jy, sk). 테스트 수정이 Linux·macOS 동작을 바꾸지 않는지, `icacls` 사용 방식이 괜찮은지 봐 주세요.
- 격리 위치에서 셸별 install/status/remove를 실행해 호환표를 `verified-local` 로 올리기
- 발견 사항 1~3번을 AidALL에 이슈로 낼지 결정. [개선 보고서](../../improvement-report.md) 4번 "Windows 테스트 실패 22건"과 이번 결과의 관계도 확인이 필요합니다.
- 저장소 README에 `autopilot/` 브랜치 안내 추가 (별도 PR)
