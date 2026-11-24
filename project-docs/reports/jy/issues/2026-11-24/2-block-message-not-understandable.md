재현 여부: 한 명(jy)이 한 PC에서 5회 중 5회 재현. 두 번째 재현자는 아직 없음.

제목: [bug]: Stop block message gives the user only an internal reason code in English

### Platform

Claude Code

### Operating system and shell

Windows 11 Pro (10.0.26200.9974, 한국어) / PowerShell 7.6.10, Claude Code CLI 2.1.310 (터미널), Python 3.14.4

### Summary

Stop `completion` 훅이 답변을 막을 때 화면에 나오는 메시지가 내부 사유 코드와 모델에게 하는 영어 지시뿐입니다. 사용자는 무엇이 막혔는지, 왜 막혔는지, 자기가 무엇을 해야 하는지 알 수 없습니다.

### Steps to reproduce

1. 기준 commit `c6019ac`(`test-baseline-2026-11-16`)의 Core와 Autopilot 0.5.1을 설치합니다(설치 명령: 확인 필요). 훅 8개가 모두 켜진 상태입니다.
2. 설치된 작업 폴더에서 `claude --model claude-opus-5-5 --permission-mode auto`로 새 세션을 엽니다.
3. `test-sandbox 폴더에 hello.txt를 만들고 내용을 hello 한 단어로 써줘. 완료 조건은 파일이 있고 내용이 정확히 hello인 것이다.`를 입력합니다.
4. Stop 훅이 답변을 막을 때 화면에 나오는 메시지를 읽습니다.

### Expected behavior

막힘 메시지에 사용자가 읽을 수 있는 말로 ① 무엇이 막혔는지 ② 왜 막혔는지 ③ 사용자가 다음에 할 일이 있는지가 들어 있습니다.

### Actual behavior

- 5회 중 3회 Stop 훅이 막았고, 3회 모두 메시지에 한국어가 한 글자도 없었습니다. 내용은 사유 코드(`route-unavailable`)와 "The current-session assistant follows task-router …"처럼 모델에게 하는 지시입니다.
- 읽기 질문(`test-fixture/README.md 파일 맨 위 제목 한 줄만 알려줘.`)에서도 5회 중 5회 같은 메시지가 나왔습니다.
- 한 번 다시 시도한 뒤 남는 알림(`systemMessage`)도 같은 형태입니다. 관찰한 코드: `route-unavailable`, `criterion-unbound`, `response-assessment-required`.

### Redacted logs or output

```text
[completion-gate] route-unavailable: This input contains native tool calls but has no current Core response-route. The current-session assistant follows task-router to register this input's work and response purpose. Retain the tool results; route registration does not require repeating the work. repair-target: response_route
delivery-route: preserve_answer_and_patch_metadata
The prior answer is visible. The current-session assistant returns only the correction required by the diagnostic, without repeating that answer. This internal record operation requires no new completion controls.
```

```text
[completion-gate] Internal response review remains unresolved: criterion-unbound: The current-session assistant follows verification-before-completion, …
```

메시지를 만드는 곳은 `_shared/claude_stop_verification_hook.py`의 `main`과 `_retry_limit_notice`입니다. 로컬에서 메시지 끝에 사유 코드별 사용자 안내(무엇이 막혔나 / 왜 / 다음에 할 일)를 붙여 다시 돌렸습니다. 수정 내용은 팀 fork PR #11의 `project-docs/reports/jy/evidence/2026-11-24/hook-fix.diff`에 있습니다.

### Safety checks

- [x] I removed secrets and private data from this report.
- [x] This is not a security vulnerability report.
