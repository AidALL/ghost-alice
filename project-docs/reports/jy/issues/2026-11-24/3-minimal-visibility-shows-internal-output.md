재현 여부: 한 명(jy)이 한 PC에서 5회 중 5회 재현. 두 번째 재현자는 아직 없음.

제목: [bug]: /visibility minimal still shows internal reason codes and adapter errors

### Platform

Claude Code

### Operating system and shell

Windows 11 Pro (10.0.26200.9974, 한국어) / PowerShell 7.6.10, Claude Code CLI 2.1.310 (터미널), Python 3.14.4

### Summary

`/visibility minimal`로 바꾼 뒤에도 Stop 훅의 내부 사유 코드와 Autopilot 어댑터 오류 문구가 숨겨지지 않고 표시 대상으로 찍힙니다.

### Steps to reproduce

1. 기준 commit `c6019ac`(`test-baseline-2026-11-16`)의 Core와 Autopilot 0.5.1을 설치합니다(설치 명령: 확인 필요). 훅 8개가 모두 켜진 상태이고, 시작할 때 표시 수준은 `dynamic`입니다.
2. 설치된 작업 폴더에서 `claude --model claude-opus-5-5 --permission-mode auto`로 새 세션을 엽니다.
3. `/visibility minimal`을 입력합니다. `~/.ghost-alice/config.json`의 `agent_visibility.profile`이 `minimal`로 바뀝니다.
4. 같은 세션에서 `test-fixture/README.md 파일 맨 위 제목 한 줄만 알려줘.`를 입력합니다.
5. 둘째 입력의 훅 기록(`~/.ghost-alice/session-logs/claude/<세션 ID>/strict-hook-output.jsonl`)에서 `visible_decision`과 출력 내용을 봅니다.

### Expected behavior

`minimal`에서는 내부 사유 코드, 원장 경로, 훅 진단 문구가 사용자 화면에 나오지 않습니다.

### Actual behavior

- 5회 중 5회 `/visibility minimal` 전환은 됐습니다(설정 값 `minimal`).
- 5회 중 5회, 둘째 입력에서 Stop `completion` 훅의 `[completion-gate] route-unavailable: …` 막힘이 `visible_decision: force_show`로 찍혔습니다. 회차마다 숨기지 않은 훅 출력 4건에 내부 사유 코드가 있었습니다.
- 답변 글에도 `response-route`, `response_route`, `session-intent` 같은 내부 용어가 섞였습니다(5회 모두).
- 읽기 입력을 막지 않도록 로컬에서 고친 뒤에도, Stop `adapter-autopilot-mode-continue` 훅의 아래 오류 문구가 `visible_decision: show`로 찍혔습니다(5회 중 5회).

### Redacted logs or output

```text
{"continue": true, "systemMessage": "adapter-autopilot-mode-continue: [autopilot-mode adapter error] error: C:\\Users\\<user>\\...\\.tmp\\session-intent\\claude\\<session-id>\\intent-events.jsonl: current input event and digest are required Ghost-ALICE Autopilot recording code cannot access recording state and parks dependent a…
```

둘째 입력에서 훅별 `visible_decision`(수정 후 1회차): UserPromptSubmit `pending-merge-prompt` hide, UserPromptSubmit `session-intent` hide, PreToolUse `adapter-autopilot-mode-prepare-origin` show, PreToolUse `tool-checkpoint` show, PostToolUse `io-trace` show, Stop `completion` show, Stop `adapter-autopilot-mode-continue` show.

화면에 실제로 그려졌는지는 훅 기록의 `visible_decision` 값으로만 확인했습니다.

### Safety checks

- [x] I removed secrets and private data from this report.
- [x] This is not a security vulnerability report.
