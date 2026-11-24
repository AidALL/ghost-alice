# jy 이슈 1~4 두 번째 재현 시도와 Ghost-ALICE 켬/끔 허위율 벤치 준비

Nov 24, 2026 · sk

- 기준 버전과 다릅니다. 실제로 쓴 버전: Core `df903bc` (브랜치 `install-skproject`, `v0.5.1-124-gdf903bc`), Autopilot 애드온 `0.5.1`. 기준은 `test-baseline-2026-11-16` (Core `c6019ac`)입니다.
- 확인 환경: Windows 11 Pro (10.0.26100.9711), PowerShell 7.7.0, Python 3.14.7, Claude Code CLI 2.1.330 (터미널 대화형, 모델 `claude-opus-5-5`, 권한 모드 `auto`)
- 관련 링크: jy 이슈 [1](../jy/issues/2026-11-24/1-read-only-input-blocked.md), [2](../jy/issues/2026-11-24/2-block-message-not-understandable.md), [3](../jy/issues/2026-11-24/3-minimal-visibility-shows-internal-output.md), [4](../jy/issues/2026-11-24/4-hooks-run-without-tools-and-three-per-tool.md), 훅 기록 집계 [hook-log-summary.md](evidence/2026-11-24/hook-log-summary.md) ([hook_log_summary.py](evidence/2026-11-24/hook_log_summary.py)), 켬/끔 벤치 [onoff/](evidence/2026-11-24/onoff/)

## 1. 목표

두 가지를 확인합니다.

- jy가 올린 이슈 1~4를 다른 PC에서 다시 재현해, 재현자가 한 명뿐인 상태를 벗어납니다.
- Ghost-ALICE를 켰을 때 추측해서 답하는 비율(허위율)이 실제로 줄어드는지 측정할 장치를 만듭니다. 그동안의 허위율 벤치는 모두 켬 조건만 있어서 끔과 비교한 적이 없습니다.

## 2. 한 일

### 이슈 1~4 재현

- 설치된 Core 폴더(`~/ghost-alice-main`)에서 대화형 `claude`를 띄우고, 세 세션을 한 번씩 실행했습니다. 세션마다 새로 생긴 `~/.ghost-alice/session-logs/claude/<세션>/` 폴더를 그 단계의 세션으로 기록했습니다(폴더 수 504 → 507).
  - 세션 A: `README.md 파일 맨 위 제목 한 줄만 알려줘.`
  - 세션 B: `파이썬에서 리스트와 튜플의 차이를 한 줄로 알려줘.`
  - 세션 C: `/visibility minimal`을 입력하고, 응답이 끝난 뒤 세션 A와 같은 문장을 입력했습니다.
- 세션 C가 끝난 뒤 표시 설정을 되돌렸습니다: `python ~/.ghost-alice/runtime/current/_shared/agent_visibility_cli.py dynamic`.
- 세 세션의 `strict-hook-output.jsonl`을 [hook_log_summary.py](evidence/2026-11-24/hook_log_summary.py)로 집계했습니다. 결과 전체는 [hook-log-summary.md](evidence/2026-11-24/hook-log-summary.md)에 있습니다. 도구 호출은 Claude Code 대화 기록(`~/.claude/projects/`)으로 확인했습니다.
- 실행 조건이 의도와 달랐던 점이 두 가지 있습니다.
  - **세션 A는 작업 폴더가 `~`였습니다.** Core 폴더로 이동하는 단계가 빠졌습니다. `~`에는 README.md가 없어서 모델이 하위 폴더의 README를 찾았습니다(Bash 3회, 첫 회 실패). 이어서 작업 기록 1회를 더 호출했습니다. 그래서 세션 A는 "파일 하나만 읽는 입력"이 아닙니다. 이 조건은 세션 C의 두 번째 턴(`head -n 5 README.md`)에서 성립합니다.
  - **전역 `~/.claude/CLAUDE.md`의 공유 작업 기록 규칙** 때문에 모델이 답변 끝에 `history_tool.py add`를 호출했습니다. 세션 A에서 1회, 세션 C에서 2회입니다. 세션 C의 두 번째 턴은 README 읽기 1회와 기록 1회로 도구 2회입니다.
- 이 설치본에 등록된 Ghost-ALICE 훅은 7개입니다: SessionStart 2개(session-start, session-receipt), UserPromptSubmit 1개, PreToolUse 1개(tool-checkpoint), PostToolUse 1개(io-trace), PostToolUseFailure 1개(io-trace), Stop 1개(completion).

### 켬/끔 허위율 벤치

- 기존 정답지 벤치(로컬 `ghost-chat` 프로젝트, `bench/questions.json` 세트 `2026-11-12.v2`, 40문항)의 코드를 바꾸지 않고 감싸는 실행기 두 개를 만들었습니다. 두 파일은 `ghost-chat` 패키지를 불러오므로 `ghost-chat/<하위 폴더>/`에 두고 실행해야 합니다. `ghost-chat`은 이 저장소에 없습니다.
  - [run_onoff.py](evidence/2026-11-24/onoff/run_onoff.py): 켬은 평소 설정 그대로 실행합니다. 끔은 같은 CLI에 `--safe-mode`를 붙여 CLAUDE.md, 스킬, 훅, 플러그인을 모두 뺍니다. 채점 세션은 두 조건 모두 같습니다(사용자 설정·훅·도구 없음).
  - [analyze_onoff.py](evidence/2026-11-24/onoff/analyze_onoff.py): 답변 세션마다 Ghost-ALICE 훅 기록과 io-trace가 있는지 확인해 조건이 실제로 갈렸는지 검사합니다. 지표마다 Wilson 95% 구간과 양측 Fisher 정확 검정을 계산합니다. 계산은 알려진 값으로 확인했습니다: `fisher(3,1,1,3)=0.4857`, `fisher(1,9,11,3)=0.0028`, `wilson(0,120)=(0, 0.031)`.
- 시험 실행으로 4문항(A01, U01, F01, H01)을 켬과 끔에서 한 번씩 돌렸습니다. 결과는 [pilot-report.md](evidence/2026-11-24/onoff/pilot-report.md)에 있습니다.
- 본 측정으로 켬·끔을 번갈아 3회씩(r1~r3) 돌리기 시작했습니다. 켬 r1(40문항)은 끝났고, 끔 r1을 22문항까지 진행한 시점에 사용자 요청으로 중단했습니다. 끔 r1은 결과 파일이 저장되기 전에 멈춰서, 문항별 판정이 실행 로그에만 남았습니다.

## 3. 결과

### 이슈 1~4 재현 판정

| # | jy 이슈 | 판정 | 근거 |
| --- | --- | --- | --- |
| 1 | 파일 하나만 읽었는데 Stop completion 훅이 `route-unavailable`로 막음 | 재현 안 됨 | Stop 4회(A 1, B 1, C 2) 모두 `decision` 없이 `continue: true`. `stop_notice.state`는 모두 `quiet`, `issues`는 빈 목록. 파일 하나를 읽은 세션 C 두 번째 턴도 막히지 않음 |
| 2 | 막힘 메시지에 한국어가 없고 사용자가 할 일이 없음 | 해당 없음 | 막힘 메시지 자체가 나오지 않음 |
| 3 | `/visibility minimal` 뒤에도 내부 사유 코드나 훅 오류 문구가 보임 | 재현 안 됨(키워드 기준) | `minimal` 적용 뒤 hide가 아닌 출력에 `completion-gate`, `autopilot`, `route-unavailable`, `intent-events`가 0건. 아래 참고 |
| 4 | 도구 없는 입력에도 훅이 3번 넘게 실행됨 | 재현됨 | 세션 B: 도구 0회인데 훅 4회(SessionStart 2, UserPromptSubmit 1, Stop 1) |
| 4 | 도구 1번에 PreToolUse·PostToolUse 훅이 합쳐 3개 실행됨 | 재현 안 됨 | 세션 A: 도구 4회에 PreToolUse 4 + PostToolUse 4(실패 1 포함) = 8회. 세션 C도 4 + 4. 도구 1회당 2개 |

이슈 3 참고 (키워드 밖의 출력):

- 세션 C에서 `/visibility minimal` 입력을 처리하는 첫 도구 호출 때는 표시 설정이 아직 `dynamic`이었습니다. 이때 tool-checkpoint가 영어 안내문 1건을 show로 냈습니다(06:19:33Z). 안내문에는 내부 사유 코드 `row-not-found`가 들어 있었습니다. 앞부분: "Ghost-ALICE intake: records that need an input that Core code matched to the host's transcript row (...) are paused in this session because an input has no matching row in the native transcript (row-not-found)."
- `minimal` 적용 뒤에는 hide가 아닌 출력이 두 가지뿐이었습니다. tool-checkpoint의 `{}` 3건과 io-trace의 `io-trace observed` 4건입니다. 이 출력이 실제 화면에 보였는지는 기록만으로 확인하지 않았습니다.

### 세션별 훅 실행

| 세션 | 입력 | 도구 호출 | 훅 실행 | 훅이 쓴 시간 합계 | PreToolUse | PostToolUse | Stop block |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | README 제목 (작업 폴더 `~`) | 4 | 12 | 4.98초 | 4 | 4 | 0 |
| B | 리스트/튜플 차이 | 0 | 4 | 1.99초 | 0 | 0 | 0 |
| C | `/visibility minimal` → README 제목 | 4 | 14 | 4.80초 | 4 | 4 | 0 |

훅별 횟수와 시간은 [hook-log-summary.md](evidence/2026-11-24/hook-log-summary.md)에 있습니다.

### 켬/끔 시험 실행 (4문항씩)

| 항목 | 켬 | 끔 |
| --- | ---: | ---: |
| Ghost-ALICE 훅 기록이 있는 답변 세션 | 4 / 4 | 0 / 4 |
| io-trace 기록이 있는 답변 세션 | 4 / 4 | 0 / 4 |
| 정답 | 4 / 4 | 4 / 4 |
| 주장 수 (허위 / 확인 불가) | 29 (0 / 0) | 24 (0 / 1) |
| 답변 사용량 / 채점 사용량 | $0.97 / $0.51 | $0.30 / $0.51 |
| 답변 시간 합계 | 133.6초 | 75.5초 |

두 조건이 실제로 갈렸습니다. 정답과 허위 주장은 차이가 없었고, 확인 불가 주장 차이는 우연 확률 Fisher p = 0.453입니다. 사용량은 구독 로그인 기준의 환산값이며 실제 청구액이 아닙니다.

### 본 측정 중 완료된 부분

| 항목 | 켬 r1 (40문항, 완료) | 끔 r1 (22문항까지, 중단) |
| --- | --- | --- |
| 판정 | 정답 39, 부분 1 (F04) | 정답 20, 부분 2 (A10, A13) — 실행 로그만 |
| 주장 | 242개: 근거 있음 238, 확인 불가 4, 허위 0 | 저장 안 됨 |
| 지어낸 답 | 0 | 0 (로그 판정 기준) |
| 답변 사용량 / 채점 사용량 | $10.05 / $5.16 | 저장 안 됨 |
| 답변 시간 합계 / 도구 호출 | 1,348초 / 163회 | 저장 안 됨 |

같은 22문항(A01~A15, U01~U05, F01, F02)만 비교하면 켬은 22/22 정답, 끔은 20/22 정답입니다. 우연 확률은 Fisher p = 0.49로, 차이가 있다고 말할 수 없습니다. 끔 쪽은 로그에 남은 판정만으로 센 값입니다.

## 4. 발견한 문제

| # | 문제 | 근거 | 영향 | 처리 |
| --- | --- | --- | --- | --- |
| 1 | 도구를 쓰지 않는 입력에도 Ghost-ALICE 훅이 4회 실행됨 (jy 이슈 4의 앞부분) | 세션 B 훅 기록: SessionStart 2, UserPromptSubmit 1, Stop 1 | 간단한 질문에도 훅 시간 약 2초 | 기록만 |
| 2 | `/visibility minimal`을 처리하는 동안(적용 직전) 내부 사유 코드 `row-not-found`가 든 영어 안내가 show로 나옴 | 세션 C 06:19:33Z tool-checkpoint 출력 | 설정을 바꾸는 순간에는 minimal이 아직 적용되지 않음 | 기록만 |
| 3 | 재현 절차의 "Core 폴더로 이동" 단계를 놓치기 쉬움 | 세션 A 대화 기록의 작업 폴더가 `~` | 세션 A가 이슈 1 조건을 만족하지 못함 | 기록만. 절차에 작업 폴더 확인 단계 추가 필요 |
| 4 | 전역 CLAUDE.md의 공유 작업 기록 규칙이 도구 호출을 늘림 | 세션 A 1회, 세션 C 2회 `history_tool.py add` 호출 | 도구 수와 훅 수 비교가 jy 환경과 달라짐 | 기록만 |
| 5 | 기준 버전 재설치 뒤 pending-merge "버림" 결정 일부가 기록되지 않음(`mark_decided`가 같은 id의 예전 항목만 고침) | [이슈 문서](issues/2026-11-24/1-mark-decided-skips-pending-duplicate-id.md), [재현 스크립트](evidence/2026-11-24/repro_mark_decided_duplicate_id.py) | 결정한 항목이 계속 미결정으로 남고 `-Status`가 warning | 기록만. 최신 `main`은 `97185bd`에서 고쳐짐 |

## 5. 확인하지 못한 것

- **Core 버전이 기준과 다릅니다.** 이번 설치본(`df903bc`)에는 기준(`c6019ac`) 이후의 변경이 들어 있습니다. 그래서 이슈 1~3이 재현되지 않은 것이 버전 차이 때문인지는 알 수 없습니다. 기준 버전으로는 이번 절차를 돌리지 않았습니다.
- 재현에 쓴 설치본(`df903bc`)에는 Autopilot 훅이 하나도 등록되어 있지 않았습니다. 같은 날 기준 버전(`c6019ac` + Autopilot `v0.5.1`)으로 다시 설치하니 jy 이슈에 나오는 훅이 등록됐습니다: PreToolUse `adapter-autopilot-mode-prepare-origin`, Stop `adapter-autopilot-mode-continue`, UserPromptSubmit `pending-merge-prompt`. jy 이슈 4의 "도구 1번에 훅 3개" 중 하나가 `adapter-autopilot-mode-prepare-origin`이므로, 이 훅이 없던 것이 그 부분이 재현되지 않은 이유일 가능성이 큽니다. 기준 버전으로 다시 실행해 확인하지는 않았습니다.
- 각 단계를 1회씩만 실행했습니다. jy는 5회입니다.
- 입력 파일이 jy와 다릅니다. jy는 16줄짜리 `test-fixture/README.md`를 썼고, 이번에는 Core 폴더의 `README.md`를 썼습니다.
- show로 기록된 훅 출력이 실제 화면에 표시됐는지는 확인하지 않았습니다.
- 켬/끔 허위율 비교는 끔 조건의 완성된 결과가 없어서 결론을 내지 못했습니다. 시험 4문항과 22문항의 로그 판정만으로는 차이를 말할 수 없습니다.
- 벤치는 헤드리스(`claude -p`)로 실행합니다. 지난 측정에서 이 방식은 입력이 사람 입력으로 확정되지 않아, 목표와 응답 경로 기록이 남지 않았습니다. 따라서 대화형으로 쓸 때의 Ghost-ALICE와 같지 않습니다.
- 켬 조건에는 Ghost-ALICE 외의 사용자 설정(공유 기록 훅, Langfuse 훅, 플러그인)도 들어 있습니다. 끔(`--safe-mode`)은 이것들도 모두 뺍니다.

## 6. 다음 단계

- 이 PC는 2026-11-24에 기준 버전(Core `c6019ac`, Autopilot `v0.5.1`)으로 다시 설치했습니다. 이 설치로 같은 절차를 5회씩 반복합니다. 작업 폴더 확인 단계를 넣고, 공유 작업 기록 규칙은 뺀 상태로 합니다.
- `mark_decided` 수정(`97185bd`)을 기준 브랜치에 옮겨 올지 팀이 정합니다.
- 켬/끔 벤치를 3회씩 다시 돌릴지 정합니다. 1회 기준으로 켬은 약 $15에 30분, 끔은 약 $8에 20분이 걸립니다. 정확도가 이미 97~100%여서 차이가 드러나지 않을 수 있습니다. 그러면 더 어려운 문항이 필요합니다.
