재현 여부: 한 명(jy)이 한 PC에서 5회 중 5회 재현. 두 번째 재현자는 아직 없음.

제목: [bug]: Five hooks run for a tool-free question and three hooks run around every tool call

### Platform

Claude Code

### Operating system and shell

Windows 11 Pro (10.0.26200.9974, 한국어) / PowerShell 7.6.10, Claude Code CLI 2.1.310 (터미널), Python 3.14.4

### Summary

도구를 쓰지 않는 한 줄 질문에도 훅이 5번 실행되고, 도구를 한 번 쓸 때마다 훅 3개가 실행됩니다. 훅 실행 시간이 응답 시간에 그대로 더해집니다.

### Steps to reproduce

1. 기준 commit `c6019ac`(`test-baseline-2026-11-16`)의 Core와 Autopilot 0.5.1을 설치합니다(설치 명령: 확인 필요). 훅 8개가 모두 켜진 상태입니다.
2. 설치된 작업 폴더에서 `claude --model claude-opus-5-5 --permission-mode auto`로 새 세션을 엽니다.
3. 도구 없는 질문: `파이썬에서 리스트와 튜플의 차이를 한 줄로 알려줘.`를 입력합니다.
4. 새 세션에서 도구 1회 질문: `test-fixture/README.md 파일이 몇 줄인지 세어줘.`를 입력합니다.
5. `~/.ghost-alice/session-logs/claude/<세션 ID>/strict-hook-output.jsonl`에서 훅별 실행 횟수와 `observed_duration_s`를 셉니다.

### Expected behavior

도구를 쓰지 않는 질문에는 도구용 관리 절차가 돌지 않고, 읽기 전용 도구 호출에는 훅이 적게 돕니다.

### Actual behavior

- 도구 없는 질문: 5회 중 5회, 도구 호출 0회인데 훅이 5번 실행됐습니다(SessionStart `session-start` 1, UserPromptSubmit `pending-merge-prompt` 1, UserPromptSubmit `session-intent` 1, Stop `completion` 1, Stop `adapter-autopilot-mode-continue` 1). 회차당 훅 시간 2.2~2.3초, 응답 시간 약 3초입니다.
- 도구 1회 질문: 5회 중 5회, 도구 1번에 훅 3개가 실행됐습니다(PreToolUse `tool-checkpoint`, PreToolUse `adapter-autopilot-mode-prepare-origin`, PostToolUse `io-trace`).
- 훅 켬 세션 109개 합계: 세 훅이 2,600번 실행됐고 1,106초가 걸렸습니다. 도구 1번에 약 1.27초입니다.
  - `tool-checkpoint` 888회, 평균 0.47초
  - `adapter-autopilot-mode-prepare-origin` 891회, 평균 0.41초
  - `io-trace` 821회, 평균 0.39초. 821회 모두 stdout이 비어 있습니다.
- UserPromptSubmit `session-intent`는 입력마다 같은 2,108자를 `additionalContext`로 넣습니다(187회 모두 같은 길이).
- 14개 사례 × 5회 합계로 훅을 끈 폴더는 783초, 켠 폴더는 3,742초였고, 그중 훅이 쓴 시간이 848초입니다.

### Redacted logs or output

```text
도구 없는 질문 1회차: Ghost-ALICE 훅 5회(SessionStart 1, UserPromptSubmit 2, Stop 2), 훅 시간 합계 2.33초, Stop 막힘 0회
도구 1회 질문 1회차(읽기 입력을 막지 않게 고친 뒤): Ghost-ALICE 훅 8회(SessionStart 1, UserPromptSubmit 2, PreToolUse 2, PostToolUse 1, Stop 2), 훅 시간 합계 3.45초, 빈 출력 PreToolUse 훅 최장 0.53초
```

훅마다 Python 프로세스를 새로 띄우는 시간이 들어 있습니다. 훅이 실제로 한 일의 시간과 프로세스를 띄우는 시간을 나눠 재지는 않았습니다.

### Safety checks

- [x] I removed secrets and private data from this report.
- [x] This is not a security vulnerability report.
