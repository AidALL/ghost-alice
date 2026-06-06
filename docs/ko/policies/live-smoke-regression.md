# Live Smoke Regression

언어: [🇺🇸 English](../../policies/live-smoke-regression.md) | 🇰🇷 한국어

이 procedure는 실제 agent runtime에서 같은 작은 request를 돌려 hook, gate, permission, completion contract가 살아 있는지 검증한다. automated unit test가 hook payload와 file contract를 확인한다면, 이 smoke는 실제 session surface에서 마지막 연결을 닫는다.
## Contents

- [Purpose](#purpose)
- [Standard Input](#standard-input)
- [Target Runtimes](#target-runtimes)
- [Expected Signals](#expected-signals)
- [Smoke Record](#smoke-record)
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

- `task-router`: first tool action 전에 routing이 보인다.
- `session-intent-analyzer`: raw prompt 저장 없이 intent delta 또는 hook observation이 기록된다.
- `boundary-contract`: read-only work에서는 `n/a` 또는 미사용 사유가 보인다.
- `failure-mode-if-wrong`: concise failure surface가 routine recovery-cost 또는 recovery-note fields 없이 보인다.
- conditional `recovery-action`: mismatch, scope reopen, external side effect, hard-to-recover action에 concrete next step이 필요할 때만 나타난다.
- `skill activation permission`: Claude Code와 Antigravity-family runtime이 core gate만 담은 줄인 allowlist 때문에 막히지 않는다.
- `verification-before-completion`: completion claim 직전에 fresh evidence가 나타난다.
- `[io-trace]`: 읽은 file, 실행한 command, load한 skill이 auditable하게 남는다.
- pending merge precheck: hook evidence가 있으면 hook-verified reuse로 분기하고, 없으면 current platform manifest를 직접 inspect한다.

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
  status: pass | fail | inconclusive
  reason:
  next owner:
```

## Failure Triage

| Symptom | Judgment | Next action |
| --- | --- | --- |
| `task-router` is not visible before the first tool action | gate routing failure | platform bootstrap 또는 hook payload를 inspect한다. |
| completion claim appears without `verification-before-completion` | completion gate failure | completion reminder와 Codex hook evidence/fallback wording을 inspect한다. |
| routine tool-checkpoint output requires recovery-cost or recovery-note fields | tool-checkpoint surface failure | `docs/ko/policies/session-gate-matrix.md`, platform bootstrap, hook message wording을 맞춘다. |
| skill activation permission allows only core gates | permission scope failure | installer hook permission sync와 platform policy files를 inspect한다. |
| smoke record stores raw prompt or transcript | audit hygiene failure | session-intent-analyzer storage contract를 먼저 fix한다. |
| Antigravity adapter is not ready | inconclusive | compatibility discovery가 끝난 뒤 같은 input을 다시 돌린다. |

## Automated Verification Boundary

Runtime smoke records는 manual evidence이며 keyword-presence unit test target이 아니다.
static hook payload와 gate wording contract는 `scripts/check_skill_gate_contract.py`, `scripts/validate_entrypoints.py`, `_shared.test_install_hooks`가 cover한다.
