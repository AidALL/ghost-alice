# Autopilot 0.5.1 Windows 검증: 테스트 실패 22건 정리와 셸별 결과

Nov 18, 2026 · kyu (Nov 19, 2026 갱신: 7절 · Nov 23, 2026 갱신: 4절 재작성, 8절, 부록 A · Nov 23, 2026 저녁 갱신: 4-1·4-2·4-3·4-7·부록 A-1 리뷰 반영, 9절)

- 기준 버전: `test-baseline-2026-11-16` (Core `c6019ac`, Autopilot `v0.5.1` `2075768`)
- 확인 환경: Windows PC 3대(W1 kyu, W2 jy, W3 sk. 모두 시스템 로캘 ko-KR, cp949)와 비교용 Linux 2곳(WSL2 Ubuntu, GitHub Actions). 상세는 4.0절
- 관련 링크: PR [#2](https://github.com/bogop8-star/ghost-alice/pull/2), PR [#4](https://github.com/bogop8-star/ghost-alice/pull/4) (둘 다 `autopilot/main` 에 합침), [발견 사항 상세](https://github.com/bogop8-star/ghost-alice/blob/autopilot/main/docs/windows-verification/2026-11-18-findings.md), [설계](https://github.com/bogop8-star/ghost-alice/blob/autopilot/main/docs/coding-convention/specs/2026-11-18-windows-verification-design.md)

## 1. 목표

단점 6번 "설치와 환경 맞추기가 어렵다" 중 Windows 부분을 다룹니다. Autopilot 0.5.1의 자동 테스트가 Windows에서 동작하는지 확인하고, Windows에서만 나는 실패를 "테스트 쪽 문제"와 "실제 동작 차이"로 나눠 정리합니다. 공식 호환표(`compatibility-matrix.json`)에서 Windows 항목이 모두 `not-run` 이던 것을 실제 근거로 채웁니다. AidALL 공식 업데이트는 따라가지 않고 11/12 설치 기준에서만 검증합니다.

## 2. 한 일

- 설치 기준 고정 확인: Core·Autopilot·설치본이 모두 11/12 설치 그대로이고 자동 업데이트 경로가 없음을 확인했습니다. Core 폴더에서 `git pull` 시 자동 재설치되는 Git 훅(`core.hooksPath`)은 꺼 두었습니다.
- 설치본과 분리된 개발 폴더(git worktree, 브랜치 `windows-verify`)에서만 작업했습니다. 어댑터 코드(`addons/`)는 바꾸지 않아 설치본 지문이 그대로입니다.
- 전체 테스트를 Windows에서 실행해 실패 22건의 원인을 하나씩 재현하고 분류했습니다.
- 테스트 쪽 차이 21건을 고쳤고, 실제 동작 차이 1건은 Windows에서 xfail(알려진 실패)로 표시했습니다.
- 테스트 실행기(`scripts/run_windows_tests.ps1`)를 보완했습니다. 파이썬과 pytest가 없으면 이유를 알려 주고 멈춥니다.
- PowerShell 5, PowerShell 7, CMD에서 각각 전체 테스트를 실행했습니다.
- PowerShell 5, PowerShell 7, CMD에서 각각 Core 설치 도구로 격리 위치에 install/status/uninstall을 실행했습니다. 실제 설치본은 실행 전후 지문으로 그대로임을 확인했습니다.
- 호환표 Windows 항목 3개를 `not-run` 에서 `verified-local` 로 올렸습니다.
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
| 수정 후 (Windows, JY PC, 11/19) | 758 | 1 | - | 1 | - |

Linux CI에서는 Windows에서 건너뛰거나 xfail로 둔 테스트까지 모두 통과했습니다. 테스트 수정이 Linux 동작을 바꾸지 않았다는 근거입니다. Linux에서 건너뛴 2건은 macOS 전용 도구(`sandbox-exec`)가 필요한 원래 테스트입니다.

셸별 격리 설치 확인도 세 셸 모두 성공했습니다. install은 Autopilot 훅 2개를 등록했고, status는 installed를 보고했고, uninstall은 훅 8개를 모두 지웠습니다(모두 종료 코드 0). 실제 사용자 설치 파일 921개와 설치 폴더 두 곳은 실행 전후 그대로였습니다. 이 근거로 호환표 Windows 항목 3개(CMD, PowerShell 5, PowerShell 7)를 `verified-local` 로 올렸습니다. 실제 Windows 실행 환경에서의 훅 동작은 호환표에 "다음에 필요한 근거"로 남겼습니다.

## 4. 발견한 문제

각 문제는 개발자가 같은 환경에서 그대로 따라 해 확인할 수 있도록 **재현 환경 → 재현 방법 → 기대 결과 → 실제 결과 → 재현 범위** 순서로 적었습니다. 재현 결과는 2026-11-23에 팀원 PC에서 다시 실행해 받은 출력입니다. 4-1·4-2·4-3·4-7은 같은 재현 키트(`ga_repro_kit.ps1`)로 W1(kyu)·W2(jy)·W3(sk)에서 실행했습니다. 원본 결과 파일: W2는 [`ga-repro-jy-20261123-1652.txt`](https://github.com/bogop8-star/ghost-alice/blob/jy/repro-kit-result-2026-11-23/project-docs/reports/jy/ga-repro-jy-20261123-1652.txt)와 [4-7 반복 결과](https://github.com/bogop8-star/ghost-alice/blob/jy/repro-kit-result-2026-11-23/project-docs/reports/jy/ga-repro-jy-4-7-20261123-1657.txt), W3는 [`ga-repro-sk-20261123-1549.txt`](https://github.com/bogop8-star/ghost-alice/blob/sk/repro-kit-result-2026-11-23/project-docs/reports/sk/ga-repro-sk-20261123-1549.txt), W1은 [`ga-repro-kyu-ps5-20261123-1515.txt`](evidence/2026-11-23/ga-repro-kyu-ps5-20261123-1515.txt). 키트 원본은 [`ga_repro_kit.ps1`](evidence/2026-11-23/ga_repro_kit.ps1). AidALL 저장소의 버그 리포트 양식(플랫폼, OS·셸, 재현 단계, 기대 동작, 실제 동작, 로그)과 같은 항목이라 이슈로 옮길 때 그대로 쓸 수 있습니다.

### 4.0 공통 정보

**검증 버전**

| 구성 | 버전 | 커밋 |
| --- | --- | --- |
| Ghost-ALICE Core | 0.5.1 + 팀 Windows 수정 (태그 `test-baseline-2026-11-16`) | `c6019ac` |
| Ghost-ALICE Autopilot | 0.5.1 공식 (태그 `v0.5.1`) | `2075768` |
| 비교용 AidALL Core 최신 | main | `5730668` (2026-11-23) |

**확인 환경**

| 이름 | OS | 지역·인코딩 | 셸 | Python | 기타 |
| --- | --- | --- | --- | --- | --- |
| W1 (kyu PC) | Windows 11 Pro 25H2 (10.0.26200) | 표시 언어 en-US, 시스템 로캘 ko-KR, ANSI·OEM 코드 페이지 949, Python 기본 인코딩 `cp949` (`locale.getpreferredencoding(False)`) | PowerShell 5.1.26100.9818, PowerShell 7.7.0, Command Prompt | 3.14.3 (훅 실행용), 3.12.5 (PATH 기본) | Git 2.55.0.windows.5, Claude Code 2.1.322 |
| W2 (JY PC) | Windows 11 Pro 25H2 (10.0.26200) | 표시 언어 en-US, 시스템 로캘 ko-KR, ANSI·OEM 코드 페이지 949, Python 기본 인코딩 `cp949` | Windows PowerShell 5.1.26100.9818 (키트 실행), PowerShell 7.6.10 | 3.14.4 | Git 2.55.0.windows.5, Claude Code 2.1.310. 평소 설치(11/19 기준 버전 재설치) 그대로 실행 |
| W3 (SK PC) | Windows 11 Pro 24H2 (10.0.26100) | 표시 언어 en-US, 시스템 로캘 ko-KR, ANSI·OEM 코드 페이지 949, Python 기본 인코딩 `cp949` | Windows PowerShell 5.1.26100.9711 (키트 실행), PowerShell 7.7.0 | 3.14.7 | Git 2.56.0.windows.4. 평소 설치와 분리된 별도 홈 폴더(`HOME`·`USERPROFILE`·`CLAUDE_CONFIG_DIR` 를 `ga-baseline-home` 으로 지정)에 기준 버전을 설치해 실행. 이 환경에는 Claude Code가 PATH에 없음 |
| L1 (비교) | WSL2 Ubuntu (커널 6.18.33.2-microsoft-standard-WSL2) | | bash | 3.14.4 | |
| L2 (비교) | GitHub Actions `ubuntu-latest` | | bash | | |

**분류**

| 구분 | 문제 | 처리 |
| --- | --- | --- |
| AidALL에 보고할 문제 (제품 코드) | 4-1, 4-3 (Windows), 4-2 (OS 무관, Windows에서 쉽게 발생) | 팀 범위 밖이라 기록만 함. 이슈 초안 준비됨 |
| AidALL에 보고할 문제 (제품 코드, 경쟁 조건) | 4-7 | 원인 줄 확인 (Windows PC 3대에서 실패, Linux 100회 통과) |
| 팀 내부에서 고친 문제 | 4-4, 4-5 | 수정 완료 |
| Windows와 무관한 관찰 (Core 입력 확정 막힘, 모델 동작) | 부록 A-1 (이전 4-6) | 기록만 함. 입력 확정 막힘은 AidALL 보고 후보 |

---

### 4-1. 실행 폴더 자리에 일반 파일이 있으면 Windows에서는 복구 안내 없이 넘어감 (Autopilot 어댑터)

- **대상:** `addons/autopilot-mode/skill/adapters/autopilot_mode.py` (Stop 훅 어댑터), Autopilot `2075768`
- **재현 환경:** W1 (PowerShell 7.7.0 / Windows PowerShell 5.1, Python 3.14.3), W2 (Windows PowerShell 5.1, Python 3.14.4), W3 (Windows PowerShell 5.1, Python 3.14.7)
- **재현 방법 (PowerShell):**
  ```powershell
  $t = Join-Path $env:TEMP 'ga-repro1'; New-Item -ItemType Directory $t -Force | Out-Null
  Set-Content (Join-Path $t 'not-a-directory') 'occupied'
  $env:GHOST_ALICE_AUTOPILOT_RUN_DIR = Join-Path $t 'not-a-directory'
  $env:GHOST_ALICE_CORE_ROOT = "$HOME\ghost-alice"
  '{"hook_event_name":"Stop"}' | python "$HOME\ghost-alice-autopilot\addons\autopilot-mode\skill\adapters\autopilot_mode.py"
  ```
- **기대 결과 (Linux와 같은 동작):** 실행 폴더 설정이 잘못됐다는 복구 안내가 나옴. 공식 테스트 `tests/test_completion_behavior_repair.py:311` 이 `payload['recovery']['status'] == 'parked'` 를 기대함.
- **실제 결과 (종료 코드 0):**
  ```
  W1: {"continue": true, "systemMessage": "", "completion_recording": {"status": "recording-unavailable", "reason": "authority-missing"}}
  W2: {"continue": true, "systemMessage": "", "completion_recording": {"status": "recording-unavailable", "reason": "authority-missing"}}
  W3: {"continue": true, "systemMessage": "", "completion_recording": {"status": "recording-unavailable", "reason": "authority-unavailable"}}
  ```
  같은 명령을 L1(WSL2 Ubuntu, Autopilot 테스트 코드 `ad51900`)에서 실행한 결과 (종료 코드 0, 출력 일부):
  ```
  L1: {"continue": true, "systemMessage": "[autopilot-mode adapter error]\nerror: [Errno 20] Not a directory: '/tmp/.../not-a-directory/approved-run.json'\nGhost-ALICE Autopilot recording code cannot access recording state and parks dependent advancement. ...", "completion_recording": {"status": "recording-error", "reason": "authority-unavailable", ...}, "recov...
  ```

  | 항목 | Windows (W1·W2·W3) | Linux (L1) |
  | --- | --- | --- |
  | `completion_recording.status` | `recording-unavailable` (기록 없음) | `recording-error` (오류) |
  | `systemMessage` | 빈 문자열 | `[autopilot-mode adapter error]` 안내 |
  | `recovery` | 없음 | 있음 |

  핵심 차이는 위 세 항목입니다. Windows에서는 설정 오류가 사용자에게 보이지 않습니다. `reason` 값은 Linux에서도 `authority-unavailable` 이므로 이 문제와 관계없습니다. W1·W2(`authority-missing`)와 W3(`authority-unavailable`)의 `reason` 차이는 W3가 별도 홈 폴더에 격리 설치한 환경이기 때문으로 봅니다(확인하지 않음).
- **원인:** 어댑터는 `<실행 폴더>/approved-run.json` 을 엽니다. 실행 폴더 자리가 일반 파일이면 Windows는 `FileNotFoundError` 를 내고, 어댑터는 이를 "실행 기록 없음"으로 처리합니다. Linux는 `NotADirectoryError` 를 내어 오류 안내로 이어집니다. 같은 스크립트를 두 OS에서 실행한 결과:

  | 동작 | W1 (Windows 25H2, Python 3.14.3) | W3 (Windows 24H2, Python 3.14.7) | L1 (Linux, Python 3.14.4) |
  | --- | --- | --- | --- |
  | `open(<파일>/'x')` | `FileNotFoundError` (errno 2) | `FileNotFoundError` (errno 2) | `NotADirectoryError` (errno 20) |
  | `os.listdir(<파일>)` | `NotADirectoryError` (errno 20, WinError 267) | `NotADirectoryError` (errno 20, WinError 267) | `NotADirectoryError` (errno 20) |

- **재현 범위:** Windows PC 3대 모두 같은 키트로 직접 재현(W2는 11/19 테스트 xfail도 재현). Windows 버전(24H2·25H2)과 Python 버전(3.14.3·3.14.7)이 달라도 같은 결과. L1에서는 같은 명령으로, L2(Linux CI)에서는 같은 테스트로 기대 동작을 확인. 출력: [`4-1-adapter-linux-vs-windows-20261123.txt`](evidence/2026-11-23/4-1-adapter-linux-vs-windows-20261123.txt)
- **영향:** 설정 오류가 조용히 묻힙니다. 데이터 손상은 확인되지 않았습니다.
- **처리:** 기록만 함. 어댑터를 고치면 설치본 지문이 바뀌어 재설치가 필요합니다. 테스트는 Windows에서 xfail.

### 4-2. 줄바꿈이 CRLF인 완료 문구 파일은 완료 기록이 거부됨 (Autopilot 완료 기록, OS 무관 · Windows에서 쉽게 발생)

- **대상:** `addons/autopilot-mode/skill/scripts/autopilot_completion.py` `publish --completion-file`, Autopilot `2075768`
- **요약:** 같은 완료 문구라도 파일 줄바꿈이 CRLF면 "검토 기록과 다르다"며 거부됩니다. **Linux에서도 똑같이 거부**되므로 Windows 전용 결함은 아닙니다. 다만 Windows에서는 흔한 저장 방법(PowerShell `Set-Content`)이 CRLF 파일을 만들기 때문에 Windows 사용자가 겪기 쉽습니다.
- **재현 환경:** W1 (Windows 11 25H2, Python 3.14.3, pytest 9.2.0), L1 (WSL2 Ubuntu, Python 3.14.4, pytest 9.2.0). 두 환경 모두 Autopilot 테스트 코드 `ad51900`(팀 `autopilot/main`), Core `c6019ac`.
- **재현 방법:** 공식 테스트 `CompletionV2BoundaryTest.test_v2_cli_explicit_schema_publishes_and_reuses_exact_reference` 와 같은 준비 과정(응답 검토 기록 → 완료 문구 생성)을 거친 뒤, 완료 문구를 표준 입력 대신 **파일로** 넘깁니다. 같은 내용을 LF로 저장한 파일과 CRLF로 저장한 파일로 각각 `publish` 를 실행합니다.
  ```text
  python autopilot_completion.py publish --schema-version autopilot-completion-receipt.v2 --receipt-token <token> --verified-at <time> --evidence-source <id> --completion-file completion.md --criterion-dependencies-json <mapping>
  ```
  재현용 테스트([`test_zz_repro_crlf_completion_file.py`](evidence/2026-11-23/test_zz_repro_crlf_completion_file.py))가 이 과정을 자동으로 합니다.
- **기대 결과:** 줄바꿈 형식과 관계없이 같은 완료 문구는 받아들여짐(종료 코드 0).
- **실제 결과:**

  | 환경 | 파일 줄바꿈 | 크기 | 종료 코드 | 출력 |
  | --- | --- | --- | --- | --- |
  | W1 (Windows) | LF | 672바이트 | 0 | (정상 기록) |
  | W1 (Windows) | CRLF | 688바이트 | **2** | `completion publication rejected: Final block must match exactly one recorded assistant review of its original proof` |
  | L1 (Linux) | LF | 672바이트 | 0 | (정상 기록) |
  | L1 (Linux) | CRLF | 688바이트 | **2** | 위와 같은 거부 메시지 |

- **원인:** `publish` 가 완료 문구를 바이트 그대로 읽어(`autopilot_completion.py:367` `sys.stdin.buffer.read()` / `Path(args.completion_file).read_bytes()`) 기록된 검토 내용과 비교합니다. 줄바꿈 정규화가 없어 CRLF 파일은 다른 문구로 판단합니다.
- **Windows에서 CRLF 파일이 생기는 경로 (W1에서 확인):**

  | 저장 방법 | 결과 |
  | --- | --- |
  | Windows PowerShell 5.1 `Set-Content -Value @('줄1','줄2')` | CRLF |
  | PowerShell 7.7.0 `Set-Content -Value @('줄1','줄2')` | CRLF |
  | 텍스트 모드로 표준 입력에 넘기기 (Python `subprocess.run(..., text=True)`) | LF가 CRLF로 바뀜 (W1·W2·W3) |
  | Git Bash 파이프 (`cat 파일 \| python autopilot_completion.py publish ... --completion-file -`) | 바이트 그대로 (672바이트). 기록 통과 |
  | PowerShell 7.7.0 파이프 (`Get-Content -Raw 파일 \| python ... --completion-file -`) | 끝에 CRLF 1개 추가 (674바이트). **기록 통과** |
  | Windows PowerShell 5.1 파이프 (같은 명령) | 맨 앞에 UTF-8 BOM(`ef bb bf`), 끝에 CRLF 추가 (677바이트). **거부:** `completion publication rejected: A retained explanation requires the current explanation route` |

  파이프 세 줄은 실제 `publish` 까지 실행한 결과입니다(재현 테스트 [`test_zz_repro_pipe_completion.py`](evidence/2026-11-23/test_zz_repro_pipe_completion.py), 출력 [`4-2-pipe-20261123.txt`](evidence/2026-11-23/4-2-pipe-20261123.txt)). 끝에 붙은 줄바꿈은 허용되지만, 중간 줄바꿈(CRLF 파일)이나 맨 앞(BOM)이 바뀌면 거부됩니다. 원인(전달 중 바이트가 바뀜)은 같은데 바뀐 위치에 따라 오류 문구가 달라, 사용자가 원인을 짐작하기 어렵습니다. 파이프 실험은 테스트 실행기 환경(`PYTHONUTF8=1`)에서 영어만 있는 문구로 했습니다.

  공식 테스트 2건(`NativeGeneratedPublicationV2Test.test_generated_publish_command_and_native_stop_apply_exact_original_proof`, `CompletionV2BoundaryTest.test_v2_cli_explicit_schema_publishes_and_reuses_exact_reference`)이 Windows에서 같은 메시지로 실패한 것도 이 경로(텍스트 모드 입력) 때문입니다.
- **재현 범위:** W1·L1에서 같은 결과. 줄바꿈만 다르면 OS와 관계없이 항상 재현됩니다(결정적). 파이프 결과는 W1에서 두 번 실행해 같았습니다.
- **실제 사용에서의 빈도 (11/23 조사):** 실제 세션 기록에서 Claude가 `publish` 를 직접 실행한 경우를 셌습니다. kyu는 Claude Code 기록 파일 54개에서 2번(둘 다 Git Bash, 근거 재사용 방식, 성공), jy·sk는 Langfuse TEAM 기록(11/16~11/23)의 셸 명령 1,478건(jy 319, sk 726, 이름 없는 기록 433)에서 0번이었습니다. 거부 문구는 어디에도 없었습니다([`4-2-frequency-20261123.txt`](evidence/2026-11-23/4-2-frequency-20261123.txt)). 지금 쓰는 방식에서는 거의 생기지 않습니다. 다만 Windows의 Claude Code는 PowerShell 도구도 쓰고(jy 셸 명령의 절반이 PowerShell), Windows PowerShell 5.1로 넘기면 거부되므로 잠재 위험은 남습니다.
- **확인하지 않은 것:** Stop 훅이 셸 없이 자동으로 기록하는 경로의 줄바꿈 처리. 평소 Windows PowerShell 5.1 창(`PYTHONUTF8` 없음)과 한글 문구에서의 파이프 결과. Langfuse 기록은 긴 내용이 잘려 일부 명령은 끝까지 보지 못했을 수 있습니다.
- **처리:** 기록만 함. 제안: 이 비교는 "검토한 그대로인지"를 확인하는 장치이므로 한쪽만 바꾸지 말고, 검토 기록과 제출 문구에 같은 규칙(맨 앞 BOM 제거, 줄바꿈 LF 통일)을 적용하거나 거부 메시지에 "줄바꿈·BOM 차이"를 알려 주기. 실제 빈도가 낮아 우선순위는 낮습니다.

### 4-3. 한국어 Windows에서 Core 응답 검토 도구가 한국어 파일을 읽지 못함 (Core)

- **대상:** `_shared/completion_assessment.py` 의 `prepare-response` / `record-response`. Core `c6019ac` 845·848·851줄. AidALL 최신 main `5730668`(2026-11-23)에도 974·977·980줄에 그대로 남아 있습니다. 설치본(`~/.ghost-alice/runtime/current/_shared/completion_assessment.py`)도 같은 코드입니다.
  ```python
  response_text=Path(args.response_file).read_text()           # 인코딩 지정 없음
  json.loads(Path(args.claims_file).read_text())
  json.loads(Path(args.judgment_file).read_text())
  ```
- **재현 환경:** W1 (PowerShell 7.7.0, Python 3.14.3), W2 (Windows PowerShell 5.1, Python 3.14.4), W3 (Windows PowerShell 5.1, Python 3.14.7). 세 PC 모두 Python 기본 인코딩 `cp949`
- **재현 방법 (PowerShell):**
  ```powershell
  $f = Join-Path $env:TEMP 'response.md'
  [IO.File]::WriteAllText($f, "작업을 완료했어요.`n", (New-Object Text.UTF8Encoding($false)))
  python "$HOME\.ghost-alice\runtime\current\_shared\completion_assessment.py" prepare-response --response-file $f
  ```
- **기대 결과:** UTF-8 파일을 읽고 다음 단계(세션 확인)로 진행.
- **실제 결과 (W1, 종료 코드 1):**
  ```
  {"status": "unavailable", "reason": "illegal multibyte sequence", "detail": "'cp949' codec can't decode byte 0xec in position 0: illegal multibyte sequence"}
  ```
- **대조 실험:** 같은 명령을 `$env:PYTHONUTF8='1'` 로 실행하면 파일 읽기를 통과하고 다음 단계에서 `{"status": "unavailable", "reason": "authority-unavailable", "detail": "Exact ledger root is required"}` 를 냅니다(세션 정보를 주지 않았으므로 정상적인 거부). 원인이 파일 읽기 인코딩임을 보여 줍니다.
- **재현 범위:** W1·W2·W3 세 PC 모두 같은 오류(`'cp949' codec can't decode byte 0xec`)와 같은 대조 결과(`PYTHONUTF8=1` 이면 통과)를 확인. 세 PC 모두 시스템 로캘 ko-KR, 코드 페이지 949입니다. 시스템 기본 인코딩이 UTF-8이 아닌 모든 Windows(한국어 cp949, 일본어 cp932, 중국어 cp936 등)와, 파일에 ASCII 밖 문자가 있는 경우에 생길 것으로 봅니다. 영어 Windows(cp1252)에서도 비ASCII 문자가 있으면 같은 종류의 오류가 날 수 있습니다(직접 확인하지 않음).
- **같은 종류의 코드 (11/23 확인):** Core `c6019ac` `_shared` 에는 인코딩을 지정하지 않은 `read_text()` 가 7곳 있습니다(응답 검토 도구 3, 훅 설정 읽기 1, 설치 도구 2, 응답 경로 1). 그중 `hook_profile_gate.py:887` 은 훅이 실행될 때마다 `~/.claude/settings.json` 을 읽습니다. 읽기에 실패하면 오류를 조용히 넘기므로(`ValueError` 처리) 훅은 멈추지 않지만, 어댑터 지문 다시 맞추기 기능이 말없이 꺼질 수 있습니다. W1의 `settings.json` 은 ASCII만 있어 지금은 영향이 없고, 최근 24시간 훅 1,523번 실행에서 실패는 없었습니다.
- **확인하지 않은 것:** `settings.json` 에 한글(경로, 권한 규칙 등)이 들어 있을 때의 훅 동작.
- **영향:** Ghost-ALICE 규칙이 요구하는 응답 검토 기록(`prepare-response`)을 한국어 답변으로는 남길 수 없습니다. kyu PC의 작업 세션 3곳(`06be5345`, `4a0fff20`, `8f601ab0`)에서 인코딩 지정 없이 실행한 응답 검토가 실제로 이 오류로 실패했고, 이후 `PYTHONUTF8=1` 로 우회했습니다(부록 A-1).
- **처리:** 기록만 함 (Core는 팀 범위 밖). 우회: `PYTHONUTF8=1`. 제안: 위 7곳 모두 `encoding="utf-8"` 지정. 영향이 확실하고 수정이 간단해 AidALL 이슈 1순위로 봅니다.

### 4-4. Windows 테스트 실행기가 Windows PowerShell 5.1에서 바로 멈춤 (팀 실행기, 수정 완료)

- **대상:** 팀이 만든 `scripts/run_windows_tests.ps1` (Autopilot 공식 코드 아님)
- **재현 환경:** W1, Windows PowerShell 5.1, pytest가 설치되지 않은 Python
- **재현 방법:** `powershell -ExecutionPolicy Bypass -File scripts\run_windows_tests.ps1` (수정 전 버전)
- **기대 결과:** pytest가 없다는 안내 후 멈춤
- **실제 결과:** `NativeCommandError` 로 즉시 종료
- **원인:** PowerShell 5에서 `$ErrorActionPreference='Stop'` 이면 외부 프로그램의 stderr 출력이 종료 오류가 되고, 외부 프로그램 인자 안의 큰따옴표가 지워집니다.
- **처리:** 수정 완료(`autopilot/main` 에 반영). PowerShell 5·7에서 pytest 없는 Python, 없는 경로, 정상 Python 세 경우를 확인했습니다.

### 4-5. CI가 AidALL 최신 Core를 받아 0.5.1 기준과 맞지 않음 (팀 CI, 수정 완료)

- **대상:** `autopilot/main` 브랜치의 GitHub Actions 워크플로
- **재현 환경:** L2 (GitHub Actions `ubuntu-latest`)
- **재현 방법:** 수정 전 워크플로로 `autopilot/main` 을 실행 (손대지 않은 브랜치에서도 같은 단계에서 실패)
- **기대 결과:** Autopilot 0.5.1과 짝이 맞는 Core로 테스트 실행
- **실제 결과:** 워크플로가 AidALL Core 최신 main을 받아 첫 단계에서 실패
- **원인:** Autopilot 0.5.1과 AidALL Core 최신 main은 버전 짝이 맞지 않습니다(두 Core는 Git 기록이 이어져 있지 않음).
- **처리:** 수정 완료. CI가 팀 태그 `test-baseline-2026-11-16` 의 Core를 받습니다.

### 4-7. 병렬 상태 갱신이 Windows에서 가끔 실패함 (Autopilot 상태 저장 코드, 원인 줄 확인)

- **대상:** `addons/autopilot-mode/skill/adapters/autopilot_storage.py` 의 `transaction()` (Autopilot `2075768`, 설치본과 같은 코드). 드러나는 테스트: `tests/test_autopilot_state.py::AutopilotStateTest::test_parallel_advance_claims_ready_item_once`
- **재현 환경:** W1 (Windows PowerShell 5.1, Python 3.14.3), W2 (Windows PowerShell 5.1, Python 3.14.4), W3 (Windows PowerShell 5.1, Python 3.14.7)
- **재현 방법 (W1, 2026-11-23):** 이 테스트만 반복 실행합니다. 재현 키트 `ga_repro_kit.ps1 -Repeat N` 이 같은 일을 합니다.
  ```powershell
  cd $HOME\ghost-alice-autopilot-win   # Windows 테스트 수정이 들어간 Autopilot 체크아웃
  .\scripts\run_windows_tests.ps1 tests\test_autopilot_state.py::AutopilotStateTest::test_parallel_advance_claims_ready_item_once -q --tb=long
  ```
- **기대 결과:** 매번 통과
- **실제 결과 (W1, 2회 중 1회 실패):**
  ```
  >       self.assertEqual(errors, [])
  E       AssertionError: Lists differ: [PermissionError(13, 'Permission denied')] != []
  tests\test_autopilot_state.py:3224: AssertionError
  FAILED tests/test_autopilot_state.py::AutopilotStateTest::test_parallel_advance_claims_ready_item_once
  ```
  W3에서도 같은 줄에서 같은 오류가 났습니다. 한국어 Windows라 메시지만 `PermissionError(13, '액세스가 거부되었습니다')` 로 다릅니다.

  테스트는 스레드 2개가 같은 실행 폴더에 `aps.advance_approved_run(run_dir)` 을 동시에 호출하고(`tasks.jsonl` 쓰기를 0.08초 늦춰 겹치게 함), 두 호출 모두 예외 없이 끝나야 통과합니다. 실패하면 둘 중 하나가 `PermissionError(13)` 을 받은 것입니다.
- **재현 범위:**

  | 실행 | 결과 |
  | --- | --- |
  | W1 전체 실행 3회 (PowerShell 5.1, 7.7.0, CMD, 11/18) | 3회 모두 통과 |
  | W1 같은 테스트만 2회 (Windows PowerShell 5.1, 11/23) | **2회 중 1회 실패** |
  | W2 전체 실행 1회 (11/19) | **실패 1** |
  | W2 같은 테스트만 5회 (11/19) | 5회 모두 통과 |
  | **W2 같은 테스트만 20회** (Windows PowerShell 5.1, Python 3.14.4, 11/23) | **20회 중 6회 실패** (3·5·10·14·17·18번째, 모두 같은 줄의 `PermissionError(13)`) |
  | W3 같은 테스트만 20회 (Windows PowerShell 5.1, 11/23) | **20회 중 1회 실패** (15번째) |
  | **W1 같은 테스트만 100회** (Windows PowerShell 5.1, Python 3.14.3, 11/23) | **100회 중 1회 실패** (30번째, 같은 `PermissionError(13)`) |
  | **L1 같은 테스트만 100회** (WSL2 Ubuntu, Python 3.14.4, 11/23) | **100회 모두 통과** |
  | L2 | 통과 |

  두 100회 실행은 같은 테스트 코드(Autopilot `ad51900`)와 같은 Core(`c6019ac`), 같은 pytest 9.2.0으로, 서로 겹치지 않게 차례로 실행했습니다(출력: [`4-7-loop-linux100-windows100-20261123.txt`](evidence/2026-11-23/4-7-loop-linux100-windows100-20261123.txt)). W2의 20회는 11/19 실패 때 쓴 테스트 폴더(`autopilot/windows-verify` `7663c9a`)와 venv로 실행했으며, 이 커밋의 테스트·어댑터 코드는 `ad51900` 과 같음을 확인했습니다.

  Windows PC 3대 모두에서 실패가 관찰됐습니다. 같은 테스트만 반복한 Windows 실행은 모두 147회이고 그중 9회가 실패했습니다(약 6%). PC마다 빈도 차이가 큽니다(W1 102회 중 2회, W3 20회 중 1회, W2 20회 중 6회). Linux는 100회 모두 통과했습니다. Windows에서만 나는 문제일 가능성이 높지만, 실패가 드물어서 Linux 100회 통과만으로 Linux에서 절대 안 난다고 단정하기는 어렵습니다(실패율이 1%라면 100회 연속 통과할 확률도 약 37%).
- **원인 (11/23 확인):** 테스트와 같은 상황을 W1에서 300회 반복하며 실패마다 오류 위치를 기록했습니다. 300회 중 7회 실패했고, 7회 모두 같은 곳이었습니다.
  ```
  autopilot_storage.py:444, in transaction
      if not (run_dir / AUTHORITY_FILE).exists() or _raw(run_dir / AUTHORITY_FILE) != descriptor:
  autopilot_storage.py:55, in _raw
      return json.loads(path.read_text())
  PermissionError: [Errno 13] Permission denied: '...\\authority.json'
  ```
  `transaction()` 은 SQLite 잠금(`BEGIN IMMEDIATE`) 안에서 444줄로 `authority.json` 을 읽습니다. 그런데 455~457줄은 잠금이 풀린 뒤에 같은 파일을 새 파일로 교체합니다(`_atomic()` 의 `os.replace`).
  ```python
      if has_run and not descriptor.get("migration_complete"):   # 455
          descriptor["migration_complete"] = True
          _atomic(run_dir / AUTHORITY_FILE, descriptor)          # 457: 잠금 밖에서 교체
  ```
  먼저 끝난 스레드가 잠금 밖에서 파일을 교체하는 순간, 잠금을 막 얻은 다른 스레드가 444줄에서 그 파일을 열면 Windows는 `PermissionError(13)` 을 냅니다. Linux의 `rename` 은 원자적이라 읽는 쪽이 옛 파일이나 새 파일 중 하나를 보게 되어 실패하지 않습니다. 재현 스크립트: [`trace47.py`](evidence/2026-11-23/trace47.py), 출력: [`4-7-trace-300-20261123.txt`](evidence/2026-11-23/4-7-trace-300-20261123.txt)
- **실제 사용에서의 영향 (코드 읽기 기준, 확인하지 않음):** 455~457줄은 실행 폴더에 `migration_complete` 표시가 없을 때, 즉 새 Autopilot 실행을 처음 저장할 때만 실행됩니다. 실제로는 새 실행을 시작하는 순간 훅 두 개가 같은 실행 폴더를 동시에 갱신할 때만 생길 수 있습니다.
- **처리:** 기록만 함(고치면 설치본 지문이 바뀜). 제안: 455~457줄의 `authority.json` 쓰기를 잠금 안으로 옮기거나, 444줄 읽기에서 `PermissionError` 가 나면 잠깐 뒤 다시 읽기. 원인 줄이 분명해 AidALL 이슈 2순위로 봅니다.

테스트 쪽 차이 21건(Windows `chmod` 한계, 텍스트 모드 줄바꿈 변환, 경로 따옴표, 절대경로, 한국어 오류 문구 등)의 상세는 [발견 사항 문서](https://github.com/bogop8-star/ghost-alice/blob/autopilot/main/docs/windows-verification/2026-11-18-findings.md) 3절에 있습니다.

## 5. 확인하지 못한 것

- 실제 Claude Code 세션에서 Windows 훅 전체 흐름. 테스트는 부품 단위 확인입니다.
- 4-2: Stop 훅이 셸 없이 기록하는 경로의 줄바꿈 처리, 평소 Windows PowerShell 5.1 창과 한글 문구에서의 파이프 결과
- 4-3: `settings.json` 에 한글이 있을 때의 훅 동작
- 4-7: 실제 Claude 세션에서 새 실행을 시작할 때 훅이 겹치는 경우가 있는지
- Linux에서 실제 Claude 세션으로 훅 동작 관찰
- 부록 A-1: 세션의 입력이 처음 확정되지 못하는 원인(어떤 입력에서 막히기 시작하는지)

## 6. 다음 단계

- AidALL 이슈 우선순위 제안: 1순위 4-3(영향 확실, 수정 간단), 2순위 4-7(원인 줄 확인), 3순위 4-1, 4순위 4-2(실제 빈도 낮음)
- 4-1·4-2·4-3·4-7을 AidALL에 이슈로 낼지 결정. 4절은 AidALL 버그 리포트 양식 항목을 따르므로 그대로 옮길 수 있습니다. 이슈 초안은 준비되어 있습니다. [개선 보고서](../../improvement-report.md) 4번 "Windows 테스트 실패 22건"과 이번 결과의 관계도 확인이 필요합니다.
- 저장소 README에 `autopilot/` 브랜치 안내와 "Core는 태그로 설치" 규칙 추가 (별도 PR)
- 부록 A-1의 입력 확정 막힘: AidALL 최신 main에서 같은 구조가 남아 있는지 확인한 뒤 보고 여부 결정

## 7. 11/19 추가 진행

- JY 검토 반영 (PR [#2](https://github.com/bogop8-star/ghost-alice/pull/2) 커밋 `62b27e3`): 테스트에서 파일 읽기를 막을 때 사용자 이름 대신 SID를 쓰고, 읽기가 실제로 막혔는지 확인한 뒤 막히지 않는 계정이면 이유를 적고 건너뛰게 했습니다. `icacls /remove:d` 동작은 주석으로 적었습니다. 관련 테스트 파일 6개에서 175개 통과, 건너뜀 2개(원래 있던 것)를 확인했습니다.
- PR #2 합침: Linux CI 검사 2개 통과 후 `autopilot/main` 에 합쳤습니다(합친 커밋 `75f8721`).
- JY PC 실행 결과 반영: 다른 Windows PC(Windows 11 Pro 25H2, Python 3.14.4, PowerShell 7.6.10)에서 758개 통과, 발견 사항 1번 xfail 재현, 가끔 실패하는 테스트 1건(발견 사항 7번)을 확인했습니다. JY도 Core `c6019ac` 로 실행해 Core 버전 차이는 결과에 영향이 없었습니다.
- 호환표 Linux 항목을 `verified-local` 로 올림 (PR [#4](https://github.com/bogop8-star/ghost-alice/pull/4)): 근거는 Linux CI 전체 테스트 통과와 WSL2 Ubuntu 격리 install/status/uninstall 성공입니다. 이로써 호환표 7개 대상이 모두 `verified-local` 입니다. "`not-run` 대상이 최소 1개 있어야 한다"는 기존 테스트는 "`not-run` 이면 반드시 blocker"로 바꿨습니다. 확인하지 않은 환경을 숨기지 못하게 하는 원래 목적은 그대로 검사합니다.
- AidALL 공식 저장소 변화 확인: 11/12 이후 Core 3커밋(모두 설치 관련), Autopilot 0커밋입니다. 기준 버전 고정은 그대로 유지합니다.

## 8. 11/23 추가 진행

- 4절을 개발자가 그대로 따라 할 수 있는 형식(재현 환경, 재현 방법, 기대 결과, 실제 결과, 재현 범위)으로 다시 썼습니다.
- 재현 키트(`ga_repro_kit.ps1`)를 만들어 W1(kyu)·W2(jy)·W3(sk) 세 PC에서 같은 항목을 실행했습니다. 세 PC 모두 기준 버전(Core `c6019ac`, Autopilot `2075768`) 일치를 키트가 확인했습니다. W3는 평소 설치와 분리된 별도 홈 폴더에서 실행했습니다.
- 4-1·4-3은 세 PC 모두 같은 결과로 재현됐습니다.
- 4-2를 실제 경로(완료 문구 파일)로 재현했습니다. Linux(WSL2)에서도 CRLF 파일이 똑같이 거부되어 "OS 무관, Windows에서 쉽게 발생"으로 고쳐 적었습니다.
- 4-7을 반복 실행했습니다. Windows 147회 중 9회 실패(세 PC 모두에서 관찰), Linux 100회 모두 통과.
- 이전 4-6(Claude 규칙 누락)은 Windows와 관계없는 모델 동작이라 부록 A-1로 옮기고, kyu·jy·sk 기록의 Stop 훅 경고 수를 함께 적었습니다.
- 증거 파일은 [`evidence/2026-11-23/`](evidence/2026-11-23/)에 있습니다.

## 9. 11/23 리뷰 반영

4절을 4-1부터 차례로 다시 검토하고, 근거가 약한 부분을 직접 확인해 고쳤습니다.

- 4-1: 같은 재현 명령을 Linux(WSL2)에서 실행해 실제 출력을 넣었습니다. 핵심 차이를 `status`·`systemMessage`·`recovery` 세 항목으로 좁히고, 관계없는 `reason` 비교는 줄였습니다.
- 4-2: PowerShell 파이프로 실제 `publish` 를 실행했습니다(Git Bash·PowerShell 7 통과, Windows PowerShell 5.1은 BOM 때문에 다른 문구로 거부). "Claude Code는 Git Bash를 쓴다"는 전제를 지우고, kyu·jy·sk 실제 세션 기록으로 발생 빈도(0건)를 적었습니다.
- 4-3: 비교 버전을 오늘 나온 AidALL 최신 main(`5730668`)으로 바꿨습니다(같은 코드가 남아 있음). 훅이 매번 실행하는 `settings.json` 읽기도 같은 방식임을 적었습니다.
- 4-7: 실패 위치를 300회 반복으로 잡아 원인 줄(444줄 읽기, 455~457줄 잠금 밖 쓰기)을 확인했습니다. 분류를 "테스트"에서 "제품 코드(경쟁 조건)"로 바꿨습니다.
- AidALL 이슈 우선순위를 6절에 적었습니다.
- 부록 A-1: 경고를 원인별로 나눴습니다. jy의 `route-unavailable` 80건 중 61건이 입력이 20건 이상 확정되지 못한 세션 3개에서 나와, 모델 누락이 아니라 Core의 입력 확정 막힘 때문으로 봅니다. kyu 세션 `95c6673a` 의 응답 검토 누락은 cp949 오류와 관계없음을 확인했습니다.

## 부록 A. Windows와 관계없는 관찰

아래 항목은 Windows 검증 중에 관찰했지만 운영체제와 관계없는 내용(Core 구조와 모델 동작)입니다. Windows 문제와 섞이지 않도록 따로 둡니다.

### A-1. Stop 훅의 규칙 누락 경고: 상당수는 입력 확정 막힘 때문이고, 순수 모델 누락은 일부 (이전 4-6)

- **대상:** Ghost-ALICE Core의 Stop 훅 완료 점검(`completion` 훅)이 낸 경고
  - `route-unavailable`: 이번 입력에 응답 경로가 등록되지 않음
  - `criterion-unbound`: 근거를 바탕으로 한 설명 답변에 응답 검토가 기록되지 않음
- **관찰 환경:** W1, Claude Code 2.1.322 데스크톱 앱(Code 탭), 모델 `claude-opus-5-5`, 작업 세션 `95c6673a-c5ea-4f56-8402-e2da6890211d`. W2(jy) 세션 18개, W3(sk) 세션은 Langfuse TEAM 프로젝트 기록(11/16~11/23).
- **기대 동작:** 도구를 쓰기 전에 응답 경로를 등록하고, 설명 답변에는 응답 검토를 기록해 경고 없이 끝남
- **경고 수 (11/23 1차 집계):** 한 번의 답변 종료 점검을 한 번으로 세고, 같은 세션·같은 시각의 중복 기록은 하나로 합쳤습니다.

  | 팀원 | 세션 수 | 완료 점검 | 경고 | 경고 내용 | 사용한 기록 |
  | --- | --- | --- | --- | --- | --- |
  | kyu | 1 (`95c6673a`) | 170 | **23 (14%)** | 응답 검토 누락 19, 응답 경로 누락 4 | PC의 훅 기록 파일(`strict-hook-output.jsonl`) |
  | jy | 18 | 397 | **91 (23%)** | 응답 경로 누락 75, 응답 검토 누락 10, 응답 검토 요구 6 | jy의 `ghost-alice-hook-monitor` 가 보낸 Stop·completion 기록 (경고 코드 칸) |
  | sk | 83 | (셀 수 없음) | 94 | 응답 경로 누락 90, 응답 검토 누락 2, 완료 근거 요구 2 | sk 전송 프로그램의 `blocked:Stop` 기록 |

  kyu와 jy·sk는 기록 출처와 세는 방법이 달라 비율을 그대로 비교하기는 어렵습니다.

- **원인별로 나눈 결과 (11/23 리뷰):**
  1. **입력 확정 막힘 (Core 구조).** Core 기준 버전은 세션에 들어온 입력을 차례로 확정하는데, 앞 입력 하나가 확정되지 못하면 뒤 입력도 모두 멈춥니다(`session-intent-analyzer/scripts/session_intent_ledger.py:1332~1334`, `resolve_staged_input()` 이 `None` 이면 `break`). 입력이 확정되지 않으면 응답 경로를 등록할 수 없어, 그 세션은 끝까지 매 답변마다 `route-unavailable` 이 납니다. jy 세션별로 "들어온 입력"과 "확정된 입력"을 비교한 결과:

     | jy 세션 묶음 | 세션 | 확정되지 않은 입력 | 완료 점검 | `route-unavailable` | `criterion-unbound` |
     | --- | --- | --- | --- | --- | --- |
     | 입력이 20건 이상 확정되지 못한 세션 (`7ea2b0f5`, `648e8c65`, `9a734b5f`) | 3 | 31, 30, 20 (`9a734b5f` 는 하나도 확정되지 않음) | 153 | **61** | 9 |
     | 나머지 세션 | 15 | 0~1 | 244 | **19 (8%)** | 4 |

     이 집계는 경고 코드를 기록의 출력·메타데이터 전체에서 찾아 셌기 때문에, 1차 집계(코드 칸 기준 75건)보다 `route-unavailable` 이 조금 많은 80건입니다([`a1_check.py`](evidence/2026-11-23/a1_check.py), 출력 [`A-1-jy-input-vs-warnings-20261123.txt`](evidence/2026-11-23/A-1-jy-input-vs-warnings-20261123.txt)). jy의 응답 경로 누락 경고 대부분은 모델이 빠뜨린 것이 아니라 입력 확정 막힘 때문으로 봅니다. 이 보고서를 작성한 kyu 세션(`8ae8df84`)도 확정되지 못한 입력이 100건 넘게 쌓여 매 답변마다 같은 경고가 납니다.
  2. **자동 실행 세션 (sk).** sk의 경고 94건은 11/16 약 4시간 동안 83개 세션에서 나왔습니다. 사람이 대화한 세션이 아니라 자동 실행한 세션으로 보입니다. Ghost-ALICE는 자동 실행 입력을 사람 입력으로 인정하지 않아 응답 경로를 등록할 수 없으므로, 이 경우도 모델 누락이 아닐 가능성이 큽니다(sk에게 확인 필요). sk의 평소 대화 세션은 훅 출력이 Langfuse에 기록되지 않아 셀 수 없었습니다.
  3. **모델 누락.** 입력이 정상적으로 확정된 jy 세션 15개에서는 완료 점검 244번 중 응답 경로 누락이 19번(8%)이었습니다. kyu 세션 `95c6673a` 의 응답 검토 누락 19번도 여기에 해당합니다. 이 세션은 응답 검토 명령 34번을 모두 `PYTHONUTF8=1` 과 함께 실행해 cp949 오류가 한 번도 나지 않았으므로, 4-3과 관계없이 기록을 남기지 않은 경우입니다.
- **4-3과의 관계:** cp949 오류로 응답 검토가 실제로 실패한 것은 kyu PC의 다른 세션 3곳(`06be5345`, `4a0fff20`, `8f601ab0`)이었습니다. 이 세션들은 실패 후 `PYTHONUTF8=1` 로 우회했습니다. 확인 스크립트: [`cp949_sessions.py`](evidence/2026-11-23/cp949_sessions.py)
- **모델:** kyu 세션은 `claude-opus-5-5`. jy·sk 세션의 모델은 기록에서 확인하지 못했습니다.
- **처리:** 기록만 함. 입력 확정 막힘은 모델이 아니라 Core 구조 문제라 AidALL에 알릴 후보입니다. AidALL 최신 main(`5730668`)에서는 이 반복문이 있던 부분의 구조가 바뀌어 같은 코드를 찾을 수 없었고, 고쳐졌는지는 확인하지 않았습니다. 11/18 보고에 적었던 "진행 방식을 바꿔 경고를 줄였다"는 내용은 바꾸기 전후 비교 수치가 없어 뺐습니다.
