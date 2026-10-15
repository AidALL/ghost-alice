# Live Smoke Regression

Language: [🇺🇸 English](../../policies/live-smoke-regression.md) | 🇰🇷 한국어

검증 담당자는 이 절차로 실제 에이전트 실행 환경에 같은 작은 요청을 보내 훅, 검사 절차, 권한과 완료 계약이 실제로 동작하는지 관측합니다. 단위 테스트는 훅 입력·출력과 파일 계약을 확인하며, 이 점검은 실제 세션에서 그 연결을 확인합니다.

## Contents

- [Purpose](#purpose)
- [Standard Input](#standard-input)
- [Target Runtimes](#target-runtimes)
- [Expected Signals](#expected-signals)
- [Verification Layers](#verification-layers)
- [Smoke Record](#smoke-record)
- [Blind Behavior Record](#blind-behavior-record)
- [Failure Triage](#failure-triage)
- [Automated Verification Boundary](#automated-verification-boundary)


## Purpose

- 지원을 주장하는 설치된 Claude Code·Codex 대상에서 같은 입력을 실행합니다. 지원 어댑터 집합은 `skill-catalog/platform-adapters.json`을 따르며 `terminal-only`는 수동 대체 경로입니다.
- README 첫 10줄 읽기처럼 작고 되돌릴 수 있는 request를 씁니다.
- summary 품질이 아니라 runtime gate가 관찰되는지를 검증합니다.
- experiment note는 smoke record로 저장하되 raw prompt, secret, full transcript는 저장하지 않습니다.

## Standard Input

검증 담당자는 각 실행 환경에서 저장소 루트를 열고 다음 요청을 보냅니다.

```text
Read the first 10 lines of README and summarize what Ghost-ALICE OS is in one paragraph. Apply verification-before-completion before completion.
```

## Target Runtimes

| Platform | Preparation | Observed surface |
| --- | --- | --- |
| Claude Code | Ghost-ALICE install 뒤 fresh session | Skill permission, SessionStart/UserPromptSubmit hook, completion reminder |
| Codex | Ghost-ALICE install 뒤 fresh session | `AGENTS.md` bootstrap, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` read records, runtime이 hooks를 지원하면 observed hook payload firing, 그렇지 않으면 explicit hookless/manual fallback wording |
| Codex native Windows | Windows native environment에서 Ghost-ALICE install 뒤 fresh session | actual hook payload firing, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` read records |


## Expected Signals

검증 담당자는 각 점검 기록에 다음 신호의 실제 관측 여부를 남깁니다.

- `agent-run result`: agent process가 timeout 안에 exit 0으로 종료하고 non-empty last-message artifact를 씁니다.
- `tool/runtime errors`: log에 tool router error, hook failure, traceback, runtime panic, cache initialization error가 없습니다.
- `harness validity`: prompt가 request를 만족하는 데 필요한 read-only tool action을 허용합니다. 모순된 prompt는 `pass`가 아니라 `invalid-harness`입니다.
- `task-router`: first tool action 전에 routing이 보입니다.
- `session-intent-analyzer`: raw prompt 저장 없이 intent delta 또는 hook observation이 기록됩니다.
- `boundary-contract`: 현재 routing-surface의 결정을 따릅니다. 읽기 전용 검증에도 계약이 필요할 수 있으며 해당 경로가 요구하지 않을 때만 `n/a`를 기록합니다.
- conditional `failure-mode-if-wrong`: side effect, forced signal, mismatch 또는 의미 있는 사용자 결정에 필요할 때 표시합니다. routine read-only checkpoint에는 `intent`와 `why`가 필요합니다.
- conditional `recovery-action`: mismatch, scope reopen, external side effect, hard-to-recover action에 concrete next step이 필요할 때만 나타납니다.
- `skill activation permission`: Claude Code가 Core 검사 스킬만 담은 축소된 허용 목록 때문에 막히지 않습니다.
- `verification-before-completion`: 워크플로가 각 완료 주장을 해당 근거와 연결하며 변경 없는 유효한 근거는 원래 출처와 시각을 유지합니다.
- `internal audit`: 화면의 `[io-trace]`를 요구하지 않고도 도구 접근 기록을 감사할 수 있습니다.
- pending merge precheck: hook evidence가 있으면 hook-verified reuse로 분기하고, 없으면 current platform manifest를 직접 inspect합니다.

## Verification Layers

| Layer | Purpose | Subject visibility | Release role |
| --- | --- | --- | --- |
| Skill pressure RED/GREEN | 공개된 pressure에서 작성된 methodology가 behavior를 바꾸는지 확인합니다. | skill과 pressure purpose가 보입니다. | authoring evidence이며 installed-behavior release evidence가 아닙니다. |
| Governance smoke | hook, gate, process health, required control-surface marker를 확인합니다. | governance request와 marker가 보입니다. | runtime plumbing evidence이며 AI behavior judgment가 아닙니다. |
| Automated blind screening | fresh session의 installed subject를 held-out authentic prompt로 평가합니다. | prompt와 ordinary runtime context만 보입니다. | 반복 가능한 behavior screening입니다. purpose, rubric, expected answer, pass criteria, prior output, experiment label은 evaluator-private 상태로 남습니다. |
| Manual clean-terminal acceptance | canonical install 뒤 사람이 새 terminal에서 held-out case를 실행합니다. | authentic request만 subject에 전달합니다. | final live acceptance gate입니다. Computer Use로 이 terminal을 자동화하지 않습니다. |

자동 비공개 평가는 에이전트 프로세스를 직접 실행합니다. 컨트롤러는 자기 입력과 평가 대상 에이전트의 입력을 구조적으로 분리합니다. 독립 평가 도구가 판단을 반환할 때까지 대상의 응답은 메모리에만 보관합니다. 컨트롤러가 봉인된 사례 파일을 직접 읽고, 읽은 정확한 바이트의 digest와 검증된 사례의 canonical hash를 계산합니다. 호출자가 공급한 freshness, isolation, digest 필드는 거부합니다. 설치 출처는 `~/.ghost-alice/install-state/<platform>.json`의 schema-version-1 설치 기록에서만 읽습니다. 호출자나 대상 에이전트의 자기 선언은 설치 출처의 근거가 아닙니다.

## Smoke Record

검증 담당자는 점검 결과를 저장소의 `.tmp/` 또는 로컬 임시 작업 공간에만 기록합니다. 원격 Wiki나 사용자 홈 설정은 변경하지 않습니다.

```text
platform:
date:
repo_ref:
input_case: README first 10 lines
observed:
  task-router:
  session-intent-analyzer:
  failure-mode-if-wrong:
  recovery-action:
  skill activation permission:
  verification-before-completion:
  internal-audit:
failure triage:
  status: pass | fail | invalid-harness | inconclusive
  reason:
  next owner:
```

local scratch summary의 minimum machine-readable fields는 다음과 같습니다.

```text
platform:
case:
status: pass | fail | invalid-harness | inconclusive
agent_command:
exit_code:
timed_out:
log_file:
output_file:
reasons:
```

## Blind Behavior Record

Blind record는 governance-smoke summary와 별도 schema를 사용합니다. case id/hash, exact-file suite digest, source/version, installed provenance, mode, verdict/dimensions, sanitized reason, minimal process status인 `exit_code`와 `timed_out`만 포함할 수 있습니다. raw prompt, rubric, expected answer, subject response나 full transcript, raw stdout/stderr log, pass criteria, prior output, experiment label, credential, evaluator-private note를 저장하지 않습니다.

컨트롤러는 영속 기록의 평가 항목 키에 제약된 불투명 ID만 사용합니다. 비공개 기준 문구를 영속 키로 사용하지 않습니다.

`_shared/blind_behavior.py` 컨트롤러가 자동 비공개 평가를 담당합니다. 비공개 평가를 `_shared/live_agent_smoke.py`의 모드로 추가하지 않습니다. 컨트롤러는 읽은 사례와 비공개 평가 자료의 형식을 검증한 뒤, 대상 실행을 `_shared/fresh_agent_session.py`에 위임합니다. 이 실행 담당자만 Claude의 no-persistence 또는 Codex의 ephemeral 명령을 구성하고, resume/session 인수를 거부하며 빈 실행 디렉터리를 만듭니다. 컨트롤러는 대상의 stdin에 실제 요청만 전달하고 사례·비공개 자료를 argv에 추가하지 않습니다. 일반 실행 환경 값과 설정된 `CLAUDE_CONFIG_DIR`, `CODEX_HOME`만 전달하며 rubric, purpose, expected-answer와 컨트롤러 비공개 환경 값은 제거합니다. 컨트롤러는 대상의 응답과 비공개 평가 상태를 별도 평가 프로세스에 전달합니다.

컨트롤러는 대상·평가 도구의 시간 초과, 0이 아닌 종료 코드, 빈 대상 응답, 잘못된 평가 출력·상태와 전체·항목별 판단 불일치를 실패로 닫고 conduct-feedback 후보를 만들지 않습니다. 평가 도구가 확인한 행동 실패만 정규 보고 전용 후보 하나를 만듭니다. 선택적인 기록 저장은 원자적으로 수행하며 민감 내용이 제거된 기록만 저장합니다.

## Failure Triage

| Symptom | Judgment | Next action |
| --- | --- | --- |
| `task-router` is not visible before the first tool action | gate routing failure | platform bootstrap 또는 hook payload를 inspect합니다. |
| completion claim appears without `verification-before-completion` | completion gate failure | completion reminder와 Codex hook evidence/fallback wording을 inspect합니다. |
| routine tool-checkpoint output requires recovery-cost or recovery-note fields | tool-checkpoint surface failure | `docs/ko/policies/session-gate-matrix.md`, platform bootstrap, hook message wording을 맞춥니다. |
| skill activation permission allows only core gates | permission scope failure | installer hook permission sync와 platform policy files를 inspect합니다. |
| smoke record stores raw prompt or transcript | audit hygiene failure | session-intent-analyzer storage contract를 먼저 fix합니다. |
| agent command times out | runtime smoke failure | 해당 실패를 조사하고 세션 게이트 매트릭스의 한정된 복구 조건을 따릅니다. 영향받은 실행 파일만 다시 설치하며 구별되는 가설과 남은 권한·자원이 있을 때 재시도합니다. |
| log contains `ERROR codex_core::tools::router`, hook failure, traceback, panic, or cache initialization error | runtime smoke failure | 나중의 다른 prompt가 pass해도 이 run은 failed로 처리합니다. failing case를 reproduce하거나 source-grounded reason으로 retire합니다. |
| output file is missing or empty | runtime smoke failure | partial log activity만으로 success를 추론하지 않습니다. agent command 또는 output path를 고친 뒤 rerun합니다. |
| Windows resolves `codex` to different shim or binary across PowerShell, CMD, and automation | harness drift | `agent_command`를 기록하고, Codex smoke에서는 의도한 `codex.cmd` shim 또는 explicit absolute path를 우선합니다. |
| prompt forbids the only available read-only method needed by the task | invalid harness | harness를 고친 뒤 rerun합니다. 이것은 product pass evidence가 아닙니다. |
| a later bounded pass follows an earlier unresolved failure in the same loop | partial status | 미해결 사례를 유지하고 부분 상태를 보고합니다. 세션 게이트 매트릭스에 따라 적격한 승인 복구만 계속하며 이후의 한정된 통과로 앞선 기준을 닫지 않습니다. |

## Automated Verification Boundary

검증 담당자는 실행 환경 점검 기록을 실제 관측 근거로 사용하며 키워드 존재 여부만 검사하는 단위 테스트로 대체하지 않습니다. `scripts/check_skill_gate_contract.py`, `scripts/validate_entrypoints.py`, `_shared.test_install_hooks`는 정적 훅 입력·출력과 검사 문구의 계약을 확인합니다. `_shared/live_agent_smoke.py`는 프로세스 근거를 분류하고 Codex의 새 세션 점검 사례를 실행하지만, 이 정책과 설계 문서의 독립 검토를 대신하지 않습니다. 자동 비공개 평가는 근거를 보강하며 사람이 깨끗한 새 터미널에서 수행하는 최종 수락 검사를 대신하지 않습니다.
