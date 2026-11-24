재현 여부: 한 명(jy)이 한 PC에서 5회 중 5회 재현. 두 번째 재현자는 아직 없음.

제목: [bug]: Stop completion hook blocks a read-only input with route-unavailable

### Platform

Claude Code

### Operating system and shell

Windows 11 Pro (10.0.26200.9974, 한국어) / PowerShell 7.6.10, Claude Code CLI 2.1.310 (터미널), Python 3.14.4

### Summary

파일 하나를 읽고 답하는 입력에서도 Stop `completion` 훅이 `route-unavailable`로 답변을 한 번 막습니다. 그 뒤 모델이 response-route를 등록하려고 도구를 추가로 호출해서, 같은 답을 내는 데 시간과 토큰이 몇 배로 듭니다.

### Steps to reproduce

1. 기준 commit `c6019ac`(`test-baseline-2026-11-16`)의 Core와 Autopilot 0.5.1을 설치합니다(설치 명령: 확인 필요). 훅 8개가 모두 켜진 상태(`GHOST_ALICE_DISABLED_HOOKS=none`)입니다.
2. 설치된 작업 폴더에서 `claude --model claude-opus-5-5 --permission-mode auto`로 새 세션을 엽니다.
3. `test-fixture/README.md 파일 맨 위 제목 한 줄만 알려줘.`를 입력합니다. `test-fixture/README.md`는 16줄짜리 Markdown 파일입니다.
4. 답변이 나온 뒤 Stop 훅 출력과 그 뒤의 도구 호출을 봅니다.

### Expected behavior

파일을 읽기만 한 입력은 막지 않고 답변을 그대로 끝냅니다. 훅을 끈 폴더에서는 같은 입력이 도구 호출 1회로 끝납니다.

### Actual behavior

- 5회 중 5회, Stop `completion` 훅이 `decision: block`을 냈습니다.
- 막힌 뒤 모델이 Skill `task-router`와 Bash `session_intent_ledger.py`, `response_route.py`를 호출했습니다. 5회 합계 도구 호출 42회(회차당 6~11회), 시간 267초입니다.
- 같은 입력을 훅을 끈 폴더에서 돌린 비슷한 사례(README 제목 읽기)는 5회 합계 도구 호출 5회, 27초입니다.
- 훅 켬 세션 109개 전체에서 Stop 막힘 89번 중 83번이 `route-unavailable`이었습니다.

### Redacted logs or output

```text
{"decision": "block", "reason": "[completion-gate] route-unavailable: This input contains native tool calls but has no current Core response-route. The current-session assistant follows task-router to register this input's work and response purpose. Retain the tool results; route registration does not require repeating the work. repair-target: response_route\ndelivery-route: preserve_answer_and_patch_metadata\nThe prior answer is visible. The current-session assistant returns only the correction required by the diagnostic, without repeating that answer. This internal record operation requires no new completion controls."}
```

막힘을 내는 곳은 `_shared/native_completion.py`의 `assess_native_completion`입니다. `native_tool_activity`가 `observed`이고 `route_binding`이 `current`가 아니면 도구 종류와 상관없이 이 문제를 냅니다.

로컬에서 "그 입력의 도구가 모두 읽기 전용이면 내지 않는다"로 바꿔 같은 사례를 다시 돌리면 5회 중 0회 막혔고, 5회 합계 도구 호출 5회, 49초였습니다. 수정 내용은 팀 fork PR #11의 `project-docs/reports/jy/evidence/2026-11-24/hook-fix.diff`에 있습니다.

### Safety checks

- [x] I removed secrets and private data from this report.
- [x] This is not a security vulnerability report.
