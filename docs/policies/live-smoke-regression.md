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

- `agent-run result`: the agent process exits 0 within the timeout and writes a non-empty last-message artifact.
- `tool/runtime errors`: the log has no tool router errors, hook failures, tracebacks, runtime panics, or cache initialization errors.
- `harness validity`: the prompt permits the read-only tool action needed to satisfy the request. A contradictory prompt is `invalid-harness`, not `pass`.
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
  status: pass | fail | invalid-harness | inconclusive
  reason:
  next owner:
```

Minimum machine-readable fields for a local scratch summary:

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

## Failure Triage

| Symptom | Judgment | Next action |
| --- | --- | --- |
| `task-router` is not visible before the first tool action | gate routing failure | Inspect platform bootstrap or hook payload. |
| Completion claim appears without `verification-before-completion` | completion gate failure | Inspect completion reminder and Codex hook evidence/fallback wording. |
| routine tool-checkpoint output requires recovery-cost or recovery-note fields | tool-checkpoint surface failure | Synchronize `docs/policies/session-gate-matrix.md`, platform bootstrap, and hook message wording. |
| skill activation permission allows only core gates | permission scope failure | Inspect installer hook permission sync and platform policy files. |
| smoke record stores raw prompt or transcript | audit hygiene failure | Fix the session-intent-analyzer storage contract first. |
| agent command times out | runtime smoke failure | Keep the loop open. Inspect the log, fix or narrow the failing behavior, reinstall, and rerun in a fresh session. |
| log contains `ERROR codex_core::tools::router`, hook failure, traceback, panic, or cache initialization error | runtime smoke failure | Treat the run as failed even if a later, different prompt passes. Reproduce or intentionally retire the failing case with evidence. |
| output file is missing or empty | runtime smoke failure | Do not infer success from partial log activity. Rerun after fixing the agent command or output path. |
| Windows resolves `codex` to different shim or binary across PowerShell, CMD, and automation | harness drift | Record `agent_command` and prefer the intended `codex.cmd` shim or an explicit absolute path for Codex smoke. |
| prompt forbids the only available read-only method needed by the task | invalid harness | Fix the harness and rerun. This is not product pass evidence. |
| a later bounded pass follows an earlier unresolved failure in the same loop | partial status | Keep the loop open until the failed case is fixed, retired with a source-grounded reason, or explicitly marked out of scope. |
| Antigravity adapter is not ready | inconclusive | Rerun the same input after compatibility discovery is complete. |

## Automated Verification Boundary

Runtime smoke records are manual evidence, not a keyword-presence unit test target.
Static hook payload and gate wording contracts are covered by `scripts/check_skill_gate_contract.py`, `scripts/validate_entrypoints.py`, and `_shared.test_install_hooks`.
`_shared/live_agent_smoke.py` may classify process-level evidence and run Codex fresh-session smoke cases, but it does not replace adversarial review of the output against this policy and the design documents.
