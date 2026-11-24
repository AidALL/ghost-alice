### Session A (b09726b9-db84-439d-ab4b-65c1bd424cf4)

| event . hook_id | runs | observed_duration_s |
| --- | ---: | ---: |
| SessionStart . session-receipt | 1 | 0.55 |
| SessionStart . session-start | 1 | 0.57 |
| UserPromptSubmit . session-intent | 1 | 0.67 |
| PreToolUse . tool-checkpoint | 4 | 0.74 |
| PostToolUseFailure . io-trace | 1 | 0.64 |
| PostToolUse . io-trace | 3 | 1.18 |
| Stop . completion | 1 | 0.63 |
| total | 12 | 4.98 |

- PreToolUse runs: 4; PostToolUse runs (including PostToolUseFailure): 4
- Stop runs: 1; decision "block": 0
  - 2026-11-24T06:11:20.789856Z: stop_notice.state=quiet, issues=[]

| ts | event . hook_id | visibility profile | issue-3 keywords | user surface (first 200 chars) |
| --- | --- | --- | --- | --- |
| 2026-11-24T06:10:58.802672Z | PreToolUse . tool-checkpoint | dynamic | - | {} |
| 2026-11-24T06:11:02.313991Z | PostToolUseFailure . io-trace | dynamic | - | (empty) |
| 2026-11-24T06:11:04.056187Z | PreToolUse . tool-checkpoint | dynamic | - | {} |
| 2026-11-24T06:11:05.781264Z | PostToolUse . io-trace | dynamic | - | (empty) |
| 2026-11-24T06:11:07.380991Z | PreToolUse . tool-checkpoint | dynamic | - | {} |
| 2026-11-24T06:11:09.528847Z | PostToolUse . io-trace | dynamic | - | (empty) |
| 2026-11-24T06:11:12.848155Z | PreToolUse . tool-checkpoint | dynamic | - | {} |
| 2026-11-24T06:11:14.341328Z | PostToolUse . io-trace | dynamic | - | (empty) |

### Session B (ac150bd8-9ea8-45f5-8111-ab3d9406ecc1)

| event . hook_id | runs | observed_duration_s |
| --- | ---: | ---: |
| SessionStart . session-receipt | 1 | 0.44 |
| SessionStart . session-start | 1 | 0.44 |
| UserPromptSubmit . session-intent | 1 | 0.45 |
| Stop . completion | 1 | 0.66 |
| total | 4 | 1.99 |

- PreToolUse runs: 0; PostToolUse runs (including PostToolUseFailure): 0
- Stop runs: 1; decision "block": 0
  - 2026-11-24T06:15:41.307915Z: stop_notice.state=quiet, issues=[]

| ts | event . hook_id | visibility profile | issue-3 keywords | user surface (first 200 chars) |
| --- | --- | --- | --- | --- |

### Session C (6e411d14-b480-460a-8531-a25cd20743a3)

| event . hook_id | runs | observed_duration_s |
| --- | ---: | ---: |
| SessionStart . session-receipt | 1 | 0.43 |
| SessionStart . session-start | 1 | 0.45 |
| UserPromptSubmit . session-intent | 2 | 1.04 |
| PreToolUse . tool-checkpoint | 4 | 0.59 |
| PostToolUse . io-trace | 4 | 1.29 |
| Stop . completion | 2 | 1.00 |
| total | 14 | 4.80 |

- PreToolUse runs: 4; PostToolUse runs (including PostToolUseFailure): 4
- Stop runs: 2; decision "block": 0
  - 2026-11-24T06:19:46.151617Z: stop_notice.state=quiet, issues=[]
  - 2026-11-24T06:20:43.131650Z: stop_notice.state=quiet, issues=[]

| ts | event . hook_id | visibility profile | issue-3 keywords | user surface (first 200 chars) |
| --- | --- | --- | --- | --- |
| 2026-11-24T06:19:33.774780Z | PreToolUse . tool-checkpoint | dynamic | - | {"systemMessage": "Ghost-ALICE intake: records that need an input that Core code matched to the host's transcript row (the response model's `interpreted-as` record, the response route and the completi |
| 2026-11-24T06:19:37.619426Z | PostToolUse . io-trace | minimal | - | io-trace observed |
| 2026-11-24T06:19:40.367158Z | PreToolUse . tool-checkpoint | minimal | - | {} |
| 2026-11-24T06:19:42.279306Z | PostToolUse . io-trace | minimal | - | io-trace observed |
| 2026-11-24T06:20:35.429972Z | PreToolUse . tool-checkpoint | minimal | - | {} |
| 2026-11-24T06:20:36.570050Z | PostToolUse . io-trace | minimal | - | io-trace observed |
| 2026-11-24T06:20:39.451029Z | PreToolUse . tool-checkpoint | minimal | - | {} |
| 2026-11-24T06:20:41.179495Z | PostToolUse . io-trace | minimal | - | io-trace observed |

