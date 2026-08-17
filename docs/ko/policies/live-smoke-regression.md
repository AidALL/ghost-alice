# Live Smoke Regression

언어: [🇺🇸 English](../../policies/live-smoke-regression.md) | 🇰🇷 한국어

이 procedure는 실제 agent runtime에서 같은 작은 request를 돌려 hook, gate, permission, completion contract가 살아 있는지 검증한다. automated unit test가 hook payload와 file contract를 확인한다면, 이 smoke는 실제 session surface에서 마지막 연결을 닫는다.
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

- Claude Code, Codex, Antigravity에서 같은 input을 실행한다.
- README 첫 10줄 읽기처럼 작고 되돌릴 수 있는 request를 쓴다.
- summary 품질이 아니라 runtime gate가 관찰되는지를 검증한다.
- experiment note는 smoke record로 저장하되 raw prompt, secret, full transcript는 저장하지 않는다.

## Standard Input

각 runtime에서 repo root를 열고 이 request를 보낸다.

```text
Read the first 10 lines of README and summarize what Ghost-ALICE OS is in one paragraph. Apply verification-before-completion before completion and include [io-trace].
```

## Target Runtimes

| Platform | Preparation | Observed surface |
| --- | --- | --- |
| Claude Code | Ghost-ALICE install 뒤 fresh session | Skill permission, SessionStart/UserPromptSubmit hook, completion reminder |
| Codex | Ghost-ALICE install 뒤 fresh session | `AGENTS.md` bootstrap, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` read records, runtime이 hooks를 지원하면 observed hook payload firing, 그렇지 않으면 explicit hookless/manual fallback wording |
| Codex native Windows | Windows native environment에서 Ghost-ALICE install 뒤 fresh session | actual hook payload firing, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` read records |
| Antigravity | adapter implementation 전에는 inconclusive. implementation 뒤 같은 prompt를 실행한다. | skill activation permission, hook or instruction-backed fallback, smoke record |

Antigravity adapter가 없으면 그 smoke item은 inconclusive로 기록한다.

## Expected Signals

각 smoke record는 다음 signal이 관찰됐는지 기록한다.

- `agent-run result`: agent process가 timeout 안에 exit 0으로 종료하고 non-empty last-message artifact를 쓴다.
- `tool/runtime errors`: log에 tool router error, hook failure, traceback, runtime panic, cache initialization error가 없다.
- `harness validity`: prompt가 request를 만족하는 데 필요한 read-only tool action을 허용한다. 모순된 prompt는 `pass`가 아니라 `invalid-harness`다.
- `task-router`: first tool action 전에 routing이 보인다.
- `session-intent-analyzer`: raw prompt 저장 없이 intent delta 또는 hook observation이 기록된다.
- `boundary-contract`: read-only work에서는 `n/a` 또는 미사용 사유가 보인다.
- `failure-mode-if-wrong`: concise failure surface가 routine recovery-cost 또는 recovery-note fields 없이 보인다.
- conditional `recovery-action`: mismatch, scope reopen, external side effect, hard-to-recover action에 concrete next step이 필요할 때만 나타난다.
- `skill activation permission`: Claude Code와 Antigravity-family runtime이 core gate만 담은 줄인 allowlist 때문에 막히지 않는다.
- `verification-before-completion`: completion claim 직전에 fresh evidence가 나타난다.
- `[io-trace]`: 읽은 file, 실행한 command, load한 skill이 auditable하게 남는다.
- pending merge precheck: hook evidence가 있으면 hook-verified reuse로 분기하고, 없으면 current platform manifest를 직접 inspect한다.

## Verification Layers

| Layer | Purpose | Subject visibility | Release role |
| --- | --- | --- | --- |
| Skill pressure RED/GREEN | 공개된 pressure에서 작성된 methodology가 behavior를 바꾸는지 확인한다. | skill과 pressure purpose가 보인다. | authoring evidence이며 installed-behavior release evidence가 아니다. |
| Governance smoke | hook, gate, process health, required control-surface marker를 확인한다. | governance request와 marker가 보인다. | runtime plumbing evidence이며 AI behavior judgment가 아니다. |
| Automated blind screening | fresh session의 installed subject를 held-out authentic prompt로 평가한다. | prompt와 ordinary runtime context만 보인다. | 반복 가능한 behavior screening이다. purpose, rubric, expected answer, pass criteria, prior output, experiment label은 evaluator-private 상태로 남는다. |
| Manual clean-terminal acceptance | canonical install 뒤 사람이 새 terminal에서 held-out case를 실행한다. | authentic request만 subject에 전달한다. | final live acceptance gate다. Computer Use로 이 terminal을 자동화하지 않는다. |

Automated blind screening은 direct process만 사용한다. controller packet과 subject packet을 구조적으로 분리하고, separate evaluator가 verdict를 반환할 때까지 subject output을 memory에만 유지한다. controller가 sealed case file을 직접 load하고 exact loaded bytes의 digest와 validated case의 canonical hash를 계산하며 caller가 공급한 freshness, isolation, digest field를 거부한다. installed provenance는 `~/.ghost-alice/install-state/<platform>.json`의 schema-version-1 installer state에서만 가져오며 caller나 subject self-attestation은 provenance가 아니다.

## Smoke Record

repo에 record를 저장할 때는 `tmp/` 또는 local scratch만 쓴다. remote Wiki나 user home settings는 mutate하지 않는다.

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
  io-trace:
failure triage:
  status: pass | fail | invalid-harness | inconclusive
  reason:
  next owner:
```

local scratch summary의 minimum machine-readable fields는 다음과 같다.

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

Blind record는 governance-smoke summary와 별도 schema를 사용한다. case id/hash, exact-file suite digest, source/version, installed provenance, mode, verdict/dimensions, sanitized reason, minimal process status인 `exit_code`와 `timed_out`만 포함할 수 있다. raw prompt, rubric, expected answer, subject response나 full transcript, raw stdout/stderr log, pass criteria, prior output, experiment label, credential, evaluator-private note를 저장하지 않는다.

Persisted dimension key는 constrained opaque id를 사용한다. private criterion text를 durable dimension key로 사용하지 않는다.

`_shared/blind_behavior.py`가 dedicated automated screening path다. blind evaluation을 `_shared/live_agent_smoke.py`의 mode로 추가하지 않는다. controller는 loaded case와 evaluator-private type을 검증한 뒤 subject execution을 `_shared/fresh_agent_session.py`에 위임한다. 이 owner만 Claude no-persistence 또는 Codex ephemeral command를 구성하고 resume/session argument를 거부하며 empty run cwd를 만든다. subject stdin에는 authentic prompt만 보내고 case/private argv를 추가하지 않는다. ordinary runtime value와 설정된 `CLAUDE_CONFIG_DIR`, `CODEX_HOME`만 전달하며 rubric, purpose, expected-answer, controller-private environment value는 제거한다. response와 private state는 separate evaluator process에 전달한다.

Subject/evaluator timeout, non-zero exit, empty subject response, malformed evaluator output, invalid evaluator state, overall/dimension disagreement는 모두 fail-closed 처리하며 conduct-feedback candidate를 만들지 않는다. Evaluator-confirmed behavioral failure만 exactly one canonical report-only candidate를 만든다. optional record write는 atomic하며 sanitized record만 저장한다.

## Failure Triage

| Symptom | Judgment | Next action |
| --- | --- | --- |
| `task-router` is not visible before the first tool action | gate routing failure | platform bootstrap 또는 hook payload를 inspect한다. |
| completion claim appears without `verification-before-completion` | completion gate failure | completion reminder와 Codex hook evidence/fallback wording을 inspect한다. |
| routine tool-checkpoint output requires recovery-cost or recovery-note fields | tool-checkpoint surface failure | `docs/ko/policies/session-gate-matrix.md`, platform bootstrap, hook message wording을 맞춘다. |
| skill activation permission allows only core gates | permission scope failure | installer hook permission sync와 platform policy files를 inspect한다. |
| smoke record stores raw prompt or transcript | audit hygiene failure | session-intent-analyzer storage contract를 먼저 fix한다. |
| agent command times out | runtime smoke failure | loop를 닫지 않는다. log를 inspect하고, failing behavior를 fix하거나 좁힌 뒤 reinstall하고 fresh session에서 rerun한다. |
| log contains `ERROR codex_core::tools::router`, hook failure, traceback, panic, or cache initialization error | runtime smoke failure | 나중의 다른 prompt가 pass해도 이 run은 failed로 처리한다. failing case를 reproduce하거나 source-grounded reason으로 retire한다. |
| output file is missing or empty | runtime smoke failure | partial log activity만으로 success를 추론하지 않는다. agent command 또는 output path를 고친 뒤 rerun한다. |
| Windows resolves `codex` to different shim or binary across PowerShell, CMD, and automation | harness drift | `agent_command`를 기록하고, Codex smoke에서는 의도한 `codex.cmd` shim 또는 explicit absolute path를 우선한다. |
| prompt forbids the only available read-only method needed by the task | invalid harness | harness를 고친 뒤 rerun한다. 이것은 product pass evidence가 아니다. |
| a later bounded pass follows an earlier unresolved failure in the same loop | partial status | failed case가 fix되거나, 근거와 함께 retire되거나, 명시적으로 out of scope 처리될 때까지 loop를 열어 둔다. |
| Antigravity adapter is not ready | inconclusive | compatibility discovery가 끝난 뒤 같은 input을 다시 돌린다. |

## Automated Verification Boundary

Runtime smoke records는 manual evidence이며 keyword-presence unit test target이 아니다. static hook payload와 gate wording contract는 `scripts/check_skill_gate_contract.py`, `scripts/validate_entrypoints.py`, `_shared.test_install_hooks`가 cover한다. `_shared/live_agent_smoke.py`는 process-level evidence를 classify하고 Codex fresh-session smoke case를 실행할 수 있지만, 이 policy와 design documents에 대한 adversarial review를 대체하지 않는다. Automated blind screening은 evidence를 강화하지만 human-operated clean-terminal final acceptance gate를 대체하지 않는다.
