# Live Smoke Regression

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/live-smoke-regression.md)

This procedure runs the same small request in real agent runtimes to verify that hook, gate, permission, and completion contracts are alive. Automated unit tests cover hook payload and file contracts; this smoke closes the final connection on the actual session surface.
## Contents

- [Purpose](#purpose)
- [Standard Input](#standard-input)
- [Target Runtimes](#target-runtimes)
- [Expected Signals](#expected-signals)
- [Smoke Record](#smoke-record)
- [Failure Triage](#failure-triage)
- [Automated Verification Boundary](#automated-verification-boundary)


## Purpose

- Run the same input in Claude Code, Codex, and Antigravity.
- Keep the request small and reversible, such as reading the first 10 README lines.
- Verify runtime gate observability, not the quality of the summary itself.
- Save experiment notes as a smoke record, but do not store raw prompts, secrets, or full transcripts.

## Standard Input

Open the repo root in each runtime and send this request:

```text
Read the first 10 lines of README and summarize what Ghost-ALICE OS is in one paragraph. Apply verification-before-completion before completion and include [io-trace].
```

## Target Runtimes

| Platform | Preparation | Observed surface |
| --- | --- | --- |
| Claude Code | Fresh session after Ghost-ALICE install | Skill permission, SessionStart/UserPromptSubmit hook, completion reminder |
| Codex | Fresh session after Ghost-ALICE install | `AGENTS.md` bootstrap, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` read records, observed hook payload firing when the runtime supports hooks; otherwise explicit hookless/manual fallback wording |
| Codex native Windows | Fresh session after Ghost-ALICE install in a Windows native environment | actual hook payload firing, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` read records |
| Antigravity | Before adapter implementation: inconclusive. After implementation, run the same prompt. | skill activation permission, hook or instruction-backed fallback, smoke record |

Before the Antigravity adapter exists, record this smoke item as inconclusive.

## Expected Signals

Each smoke record captures whether these signals were observed:

- `task-router`: routing is visible before the first tool action.
- `session-intent-analyzer`: intent delta or hook observation is recorded without raw prompt storage.
- `boundary-contract`: for read-only work, `n/a` or an explicit reason for non-use is visible.
- `failure-mode-if-wrong`: concise failure surface is visible without routine recovery-cost or recovery-note fields.
- conditional `recovery-action`: appears only when mismatch, scope reopen, external side effect, or hard-to-recover action needs a concrete next step.
- `skill activation permission`: Claude Code and Antigravity-family runtimes are not blocked by a shortened allowlist that covers only core gates.
- `verification-before-completion`: fresh evidence appears immediately before the completion claim.
- `[io-trace]`: files read, commands run, and skills loaded remain auditable.
- pending merge precheck: branch to hook-verified reuse when hook evidence exists; otherwise inspect the current platform manifest directly.

## Smoke Record

When storing a record in the repo, use only `tmp/` or local scratch. Do not mutate the remote Wiki or user home settings.

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
| `task-router` is not visible before the first tool action | gate routing failure | Inspect platform bootstrap or hook payload. |
| Completion claim appears without `verification-before-completion` | completion gate failure | Inspect completion reminder and Codex hook evidence/fallback wording. |
| routine tool-checkpoint output requires recovery-cost or recovery-note fields | tool-checkpoint surface failure | Synchronize `docs/policies/session-gate-matrix.md`, platform bootstrap, and hook message wording. |
| skill activation permission allows only core gates | permission scope failure | Inspect installer hook permission sync and platform policy files. |
| smoke record stores raw prompt or transcript | audit hygiene failure | Fix the session-intent-analyzer storage contract first. |
| Antigravity adapter is not ready | inconclusive | Rerun the same input after compatibility discovery is complete. |

## Automated Verification Boundary

Runtime smoke records are manual evidence, not a keyword-presence unit test target.
Static hook payload and gate wording contracts are covered by `scripts/check_skill_gate_contract.py`, `scripts/validate_entrypoints.py`, and `_shared.test_install_hooks`.
