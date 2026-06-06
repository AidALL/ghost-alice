# Session Gate Matrix

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/session-gate-matrix.md)

The session gate SSOT is `skill-catalog/session-gates.json`. This document is the human-readable matrix.
## Contents

- [Required Entrypoints](#required-entrypoints)
- [Turn Routing Contract](#turn-routing-contract)
- [Session Intent Ledger Contract](#session-intent-ledger-contract)
- [Runtime Hook Graph Contract](#runtime-hook-graph-contract)
- [Dynamic Focus Contract](#dynamic-focus-contract)
- [Routing Surface Contract](#routing-surface-contract)
- [Work-Impact Projection Contract](#work-impact-projection-contract)
- [Runtime Checkpoints](#runtime-checkpoints)
- [tool-checkpoint Visible Surface](#tool-checkpoint-visible-surface)
- [tool-checkpoint Batch / Continuation Compression](#tool-checkpoint-batch-continuation-compression)
- [Notes](#notes)


## Required Entrypoints

| Situation | Required entrypoint |
| --- | --- |
| First intake for every user input | `session-intent-analyzer` |
| First agent routing step after session-intent intake and jailbreak-detector downstream gate, before downstream work/tool calls | `task-router` |
| Development/coding request | `coding-convention/using-coding-convention` |
| Bug fix | `coding-convention/systematic-debugging` |
| Production code change | `coding-convention/test-driven-development` |
| Non-empty final response, including completion claims, recommendations, choices, or success judgments | `coding-convention/verification-before-completion` |
| Immediately before commit/push | `coding-convention/finishing-a-development-branch` |
| When defining a new task, sub-task, or follow-up item | `necessity-gate` |
| Immediately after task-router routes `boundary-contract: required` | `boundary-contract` |

## Turn Routing Contract

session-intent-analyzer intake is first. After pending-merge precheck, session-intent-analyzer fans out to `skill-evolution` (report-only terminal branch) and `jailbreak-detector`; task-router is released after session-intent preflight when no current-lineage block gate exists. `downstream-gates.json` may be absent on allow turns; that absence is silent allow unless a current-lineage model block is recorded. Describing the flow as `task-router → session-intent-analyzer`, as a linear `session-intent-analyzer → skill-evolution → jailbreak-detector → task-router` chain, or as task-router bypassing the jailbreak-detector downstream gate is a contract violation.

`task-router` is not a classifier used only when work looks large. It applies after session-intent-analyzer intake and jailbreak-detector downstream gate, before downstream work or tool calls on every turn where user input is detected.
`task-router` is a consumer of `session-intent-analyzer` and `jailbreak-detector/downstream-gates.json` context and an agent-side request decomposition step. It does not own user-input intake, raw intent inference, ledger updates, jailbreak decisions, downstream gate state, or tool permission.
The task-router reminder hook must withhold task-router until session-intent preflight exists and the current-lineage block check can run. If no current-lineage block gate exists, absent `downstream-gates.json` is silent allow. Once released, task-router reads the session intent ledger, performs atomic meaning decomposition, and assigns output, verification, lifecycle, and boundary skills.

The following are all fresh routing targets:

- simple questions
- opinions
- status comments
- follow-up questions from previous work
- judgment requests such as "is this right?"
- quick checks or smoke tests

The fact that a previous turn was routed is not evidence for the next turn. If hook payload provides a contract that the current platform pending-merge precheck ran and no pending warning exists, record `merge-companion-precheck: clean (hook-verified)` and do not repeat the shell manifest check. Read the current platform manifest directly only when hook evidence is absent or the environment is hookless/manual.

Runtime smoke uses `docs/policies/live-smoke-regression.md`. That procedure sends the same README first 10 lines input to Claude Code, Codex, and Antigravity and observes `task-router`, `verification-before-completion`, the concise tool-checkpoint failure surface, and skill activation permission signals.

## Session Intent Ledger Contract

`session-intent-analyzer` updates a session intent ledger for every user input. Hooks store only input digest and event metadata, never raw prompts. When the user's goal, constraints, decisions, or non-goals change, the agent records a compressed delta in `intent-state.json`.

intent-state.json is update-plus-accumulate state. Latest scalar intent fields such as `current_goal` and `user_intent_summary` are replaced by newer semantic deltas, while list-like fields such as constraints, non-goals, open questions, acceptance criteria, consumer hints, and active decisions are merged by stable identity or deduped value. `intent-events.jsonl` is append-only audit metadata, not a raw prompt transcript.

- Storage location: `.tmp/session-intent/<platform>/<session-id>/` under the Ghost-ALICE repo root
- Consumers: `skill-evolution`, `jailbreak-detector`
- Forbidden: raw prompt, full conversation, tool output, system/developer instruction text, secret values
- Hookless/manual environment: apply the session-intent-analyzer storage contract manually before the first response.


First-entry intake invariant:
- Every user input is connected to the session-intent intake path first.
- After pending-merge precheck, session-intent-analyzer fans out to `skill-evolution` (report-only terminal branch) and `jailbreak-detector`; task-router follows only after session-intent preflight and no current-lineage block gate.
- `skill-evolution` is a report-only terminal branch from session-intent-analyzer and does not feed task-router.
- Missing `current-session.json`, `intent-state.json`, hook payload, preflight evidence, or semantic delta evidence is not a deny reason for first entry.
- Missing session-intent evidence means intake/bootstrap must run or continue; it does not mean tool-checkpoint may infer risk from absence.
- tool-checkpoint must not predict the user's next input or treat unknown intent as a block condition before intake.

## Runtime Hook Graph Contract

Pending-merge precheck runs before the user-input governance graph begins. It is a pre-routing/session-start layer, not a downstream gate opened by `session-intent-analyzer`, `skill-evolution`, `jailbreak-detector`, or `tool-checkpoint`. If an undecided entry exists, the runtime must surface `merge-companion` first, but a user-explicit defer/skip may continue with that entry still undecided.

After pending-merge precheck is either clean or explicitly deferred by the user, the user-input governance graph is ordered by user intent first, then downstream gate state, then tool-stage tool-checkpoint.
`tool-checkpoint` is a PreToolUse/BeforeTool checkpoint, not part of user-input intake order. When surfaced, the visible control schema must include `hook-stage: PreToolUse` for Claude/Codex and `meaning: tool-call retry checkpoint, not user-input intake`.

1. User input triggers `session-intent-analyzer` on every turn. The hook writes the input digest, session ledger, and `current-session.json` pointer, then allows the agent turn to continue.
2. `skill-evolution` and `jailbreak-detector` consume the same session temp files keyed by platform and session id. skill-evolution is report-only and self-terminating; it does not open or close downstream gates and does not feed task-router.
3. jailbreak-detector records `model_security_decision` in the ledger and only current-lineage block decisions are carried to `downstream-gates.json` as the downstream gateway. Deterministic hard-block rules are narrow regression guards for explicit high-confidence attack signals, not proof that all jailbreak attempts are blocked. A gate block is derived only from the model's recorded judgment (`model_security_decision`), not from deterministic text matching. Gradual multi-turn jailbreak resistance depends on session-intent summary quality and cumulative constraint comparison. If a current-lineage block gate exists, tool execution is denied before tool-shape review; if no current block gate exists, absent `downstream-gates.json` is silent allow.
4. The task-router reminder hook waits until session-intent preflight exists and the current-lineage block check has had its first chance to run. A current-lineage block gate with `opened=false` or `decision=block` withholds task-router; an absent `downstream-gates.json` is silent allow. After release, `task-router` reads the current-session pointer and session intent ledger, decomposes the accepted intent into atomic meaning units, and assigns output, verification, lifecycle, and boundary skills. It does not infer raw user intent or decide tool permission.
5. `tool-checkpoint` runs at tool stage after session-intent-analyzer intake and jailbreak-detector downstream gate have had the first chance to run. Missing intake evidence starts bootstrap/recovery; it is not a reason to put `task-router` before `session-intent-analyzer` or before `jailbreak-detector`. The runtime decision reads only the current-lineage block gate: `opened=false` or `decision=block` denies, absent gate or every other state silently allows. Tool-call identity, payload content, and audit/log/correlation metadata are not decision inputs; audit/log/correlation metadata belongs outside the decision path.

## Dynamic Focus Contract

Dynamic focus control is part of the session gate contract. Work is split into semantic atoms, but the scope of attention is not fixed and does not expand in only one direction. User interaction, mismatch location, verification burden, and recovery cost can move the current focus across micro, meso, macro, and meta layers.

- micro: a tool call, command result, format check, or single semantic atom
- meso: a sub-task, boundary-contract surface, or local source-target mapping
- macro: integrated output, SSOT alignment, user constraint alignment, or cross-document logic
- meta: task necessity, task definition, scope expansion, or premise validity

When mismatch appears, the runtime procedure reopens the smallest layer that contains the cause. If the larger premise or integrated logic is wrong, macro or meta is repaired. If the atomic output or local sub-task is wrong, micro or meso is repaired. `calls` remains static and sparse; repeated focus movement, scope reopen point handling, and re-verification loops belong to procedure and runtime verification.

## Routing Surface Contract

`task-router` owns the reusable work judgment for the current turn. After it
reads session intent and downstream gate context, it emits `routing-surface`.
`session-intent-analyzer` records semantic facts and accumulated decisions, not
display recommendations. The governance surface policy consumes
`routing-surface`; it must not recompute a competing task-complexity scale.
Stable contract phrase: task-router owns the reusable work judgment; session-intent-analyzer records semantic facts and accumulated decisions; governance surface policy consumes routing-surface.

`routing-surface` reuses existing signals: `change-depth` uses
`minimal | localized | structural | systemic`, `focus-layer` uses
`micro | meso | macro | meta`, and `verification-complexity` maps to
task-complexity-level-1 through task-complexity-level-3. If a value is unknown,
ambiguous, or contradicts later evidence, consumers fail closed to fuller
surface and reopen focus through the existing scope reopen point.

An already agreed direction is an input fact, not the goal of this surface.
`accepted-continuation` is valid only when session-intent facts contain a
recorded prior proposal and recorded acceptance, such as an active decision or
acceptance criterion. It can reduce repeated re-explanation of the agreed
direction, but it does not lower the verification-complexity level required for
the output.

Token reduction is a secondary consequence, not a success metric. A smaller
screen surface is valid only when the required gates, verification, forced-risk
output, and strict-grade logs remain intact.
Stable contract phrase: token reduction is a secondary consequence, not a success metric.

## Work-Impact Projection Contract

Work-Impact Projection classifies hook-internal values by whether they change
the next work decision. A value matters when it can alter the work boundary,
focus layer, verification burden, or recovery path.
Stable contract phrase: change the work boundary, focus layer, verification burden, or recovery.

- Hook execution and the strict audit log are never reduced.
- `agent_visibility.profile` selects user-screen verbosity. It does not gate
  hook execution, strict logging, or work-impact classification.
- Forced/risk/gate values and failed verification always break through as
  user surface forced and model hint full.
- Routine/debug values stay full in the strict log, but they are omitted from
  model hints unless they change focus, boundary, verification, or recovery.
- Low-usefulness suspects such as duplicate reminders, clean-pass status,
  noop audit rows, debug counters, correlation ids, and historical wording
  are `routine` or `audit-only` by default. Promote them only when they change
  the active boundary, focus target, verification burden, or recovery action.
- Unknown, ambiguous, or failed values fail closed to fuller surface and reopen
  focus through the existing scope-reopen path.
- Goal: hook values should drive focus, boundary, verification, and recovery
  only when they can change task quality. Token reduction is a secondary
  consequence, not a success metric.
Stable contract phrase: Token reduction is a consequence, not a success metric.

## Runtime Checkpoints

Ghost-ALICE OS documents use an English canonical narrative + English control surface as the default coordination contract. The reader-facing documentation tree also keeps paired Korean counterparts where the tree exposes a pair. Field names, enum values, literal tokens, gate schemas, and allowed/forbidden values stay English and are not translated.

The first commentary must include this block:

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: session-intent-analyzer (this turn); task-router (this turn); using-coding-convention (this turn) | n/a
- next-required: <skill-name|none>
```

In a non-empty final response, including completion claims, recommendations, choices, or success judgments, include this block:

```text
[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - <criterion-id>: <user-intent-or-contract-condition> [source: user-explicit | inferred | previous-tool | system-doc]
- claim-evidence-map:
  - claim: <completion-or-recommendation-claim>
    criterion: <criterion-id>
    evidence: <fresh command, inspected file, source locator, or tool output>
    verdict: pass | fail
- unverified:
  - none
- evidence: <fresh command or inspected file>
```

`acceptance-criteria` are verifiable criteria extracted from user intent, locked decisions, and boundary-contract. `claim-evidence-map` links each final-response claim to a criterion and fresh evidence. If `unverified` is not `none`, do not speak as though completion, success, or recommendation is settled; report the partial state and remaining verification. A finalized `[completion-check]` uses `verdict: pass | fail` and `unverified: none`; an unverified item means you are not finalizing, so report partial state in prose rather than emitting `[completion-check]`. Installed Stop completion hooks run in mandatory final-block mode: a non-empty final response without `[completion-check]` is rejected, while an empty transcript is allowed.

Hard sequence: skill load/call -> fresh verification -> [completion-check]. Before a non-empty final response, load or call `verification-before-completion` for the current turn, run and read the fresh verification, and only then write `[completion-check]` with `skill-call: verification-before-completion (this turn)`. If any step is missing or out of order, the completion-check is invalid.

The `skill-call:` line records that the workflow for that skill was actually executed through the platform's skill execution mechanism in this turn. On Claude Code, write it only after a visible Skill call. On Codex, where no visible Skill tool exists, write it only after reading that skill's `SKILL.md` in this turn and following the workflow.

`verification-before-completion` is the always-on lifecycle gate before a non-empty final response, including completion claims, recommendations, choices, and success judgments. On a platform with a visible skill surface such as Claude Code `Skill`, do not write `skill-call: verification-before-completion (this turn)` in `[completion-check]` before the actual call. On Codex, write the same record only when that `SKILL.md` was actually read and the workflow followed in this turn.

If `[completion-check]` claims `skill-call: verification-before-completion (this turn)`, the same final response's `[io-trace]` `skills-loaded` must include `verification-before-completion`. Where a Stop completion hook validates the final response, this mismatch or a missing `[completion-check]` can cause retry loops.

In Codex environments without a visible Skill surface:

- Read the relevant `SKILL.md` before marking a required gate done.
- Do not mark a gate complete because of metadata, descriptions, memory, prior turns, or "I already know it."
- If the `SKILL.md` was not read in this turn, do not list the skill in `skill-call:`; the gate is still pending.
- Apply the same standard to simple tasks, already-routed tasks, and cases where metadata appears sufficient.

## tool-checkpoint Visible Surface

`tool-checkpoint` is a PreToolUse/BeforeTool checkpoint. It is not part of the user-input intake order and must not be described as running before `session-intent-analyzer`, `jailbreak-detector`, or `task-router`.

The default `[tool-checkpoint]` block requires these decision fields: `intent`, `why`, `procedure`, `contract-ref`, `contract-check`, `localized-human-note`, `rejected-alternatives`, `unverified-premises`, `failure-mode-if-wrong`. These fields show what the agent is doing, why the action is inside the active boundary, which alternatives were rejected, which premises remain unverified, and what can fail if the judgment is wrong.

`recovery-action` is conditional. Add it only when the failure mode requires a concrete recovery step, scope reopen, external side effect handling, or another hard-to-recover action. Keep it as a stable English action phrase or slug. If mismatch changes scope, state the `focus-layer` and `scope-reopen` target in `procedure` or `recovery-action`.

Do not require separate `recovery-cost` or `recovery-note` fields in routine tool checkpoints. Recovery cost belongs to work-impact projection, verification planning, or high-impact exception handling, not every PreToolUse message. Human-facing recovery explanation belongs in `localized-human-note` or the conditional `recovery-action` only when it changes the operator's next decision.

## tool-checkpoint Batch / Continuation Compression

`[tool-checkpoint:batch]` and `[tool-checkpoint:continuation]` are compact forms to reduce repeated full-gate cost. They do not create new permission and do not infer that a new tool action is safe.

`[tool-checkpoint:continuation]` refers only to output polling for the same process/session/tool-call id that was already started by a full `[tool-checkpoint]`. New command, input, timeout, interruption, or ref changes must return to full `[tool-checkpoint]`.

Simple polling of the same ref is not a duty to repeat output forever; the compact form avoids repeating the full gate. Expose it on the first poll or when state changes.

## Notes

- Treat recommendations as claims that require verification, not as casual opinions.
- Do not skip fresh verification merely because the same session just inspected something.
- Reapply `task-router` for each turn.
- `task-router` decides only whether `boundary-contract` is required. `boundary-contract` owns allowed-surface, file names, and test-purpose.
- If task-router outputs `boundary-contract: required`, the next required gate is boundary-contract.
- The `skill-call:` field structurally prevents mixing form completion with actual obligation completion. It was introduced after observing that prose-only reminders still allowed omissions within the same turn.
- Metadata-only skill matching is candidate discovery, not execution. Required gate skills are not complete unless the actual `SKILL.md` file was read and the workflow followed.
