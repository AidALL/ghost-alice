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
- [tool-checkpoint Batch / Continuation Compression](#tool-checkpoint-batch--continuation-compression)
- [Notes](#notes)


## Required Entrypoints

| Situation | Required entrypoint |
| --- | --- |
| First intake for every user input | `session-intent-analyzer` |
| First agent routing step after exact session-intent intake, before downstream work/tool calls | `task-router` |
| Development/coding request | `coding-convention/using-coding-convention` |
| Bug fix | `coding-convention/systematic-debugging` |
| Production code change | `coding-convention/test-driven-development` |
| Final response claiming executed work is complete, fixed, successful, or freshly verified | `coding-convention/verification-before-completion` |
| Immediately before commit/push | `coding-convention/finishing-a-development-branch` |
| When defining a new task, sub-task, or follow-up item | `necessity-gate` |
| Immediately after task-router routes `boundary-contract: required` | `boundary-contract` |

## Turn Routing Contract

session-intent-analyzer intake is first inside the user-input governance graph. Session-intent intake feeds task-router directly. The same exact session supplies the report-only skill-evolution terminal branch, which does not feed task-router. Task-router owns routing and current work selection, not intake, raw intent inference, credentials, or native tool permission. A hook pending-merge result precedes it; without hook evidence, the manual fallback completes before actionable work except no-work terminal routes.

Session-intent intake feeds task-router directly. The same exact session supplies the report-only skill-evolution terminal branch, which does not feed task-router. Task-router owns routing and current work selection, not intake, raw intent inference, credentials, or native tool permission. It applies to every user input after exact intake and before downstream work or tool calls, including bounded explanation and clarification routes.

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

`session-intent-analyzer` updates a session intent ledger for every user input. Hooks store only input digest and event metadata, never raw prompts. When the user's goal, constraints, decisions, or non-goals change, the agent records a compressed delta through the authoritative ledger API in `ghost-state.sqlite3`. JSON files are legacy inputs or explicit exports.

intent-state.json is update-plus-accumulate state. Latest scalar intent fields such as `current_goal` and `user_intent_summary` are replaced by newer semantic deltas, while list-like fields such as constraints, non-goals, open questions, acceptance criteria, consumer hints, and active decisions are merged by stable identity or deduped value. The ordered database events are append-only audit metadata, exposed through `--read-events` and explicit `intent-events.jsonl` exports, not a raw prompt transcript.

- Runtime storage: `ghost-state.sqlite3` under the receipt-bound ledger root; per-session JSON paths are import/export locators
- Consumers: `task-router`, report-only `skill-evolution`
- Forbidden: raw prompt, full conversation, tool output, system/developer instruction text, secret values
- Hookless/manual environment: apply the session-intent-analyzer storage contract manually before the first response.


First-entry intake invariant:
- Every user input is connected to the session-intent intake path first.
- Session-intent intake feeds task-router directly. The same exact session supplies the report-only skill-evolution terminal branch, which does not feed task-router. Task-router owns routing and current work selection, not intake, raw intent inference, credentials, or native tool permission.
- `skill-evolution` is a report-only terminal branch from session-intent-analyzer and does not feed task-router.
- Missing `current-session.json`, `intent-state.json`, hook payload, preflight evidence, or semantic delta evidence is not a deny reason for first entry.
- Missing session-intent evidence means intake/bootstrap must run or continue; it does not mean tool-checkpoint may infer risk from absence.
- tool-checkpoint must not predict the user's next input or treat unknown intent as a block condition before intake.

## Runtime Hook Graph Contract

Hook pending-merge precheck is a pre-routing/session-start layer. When hook evidence is absent, the manual fallback still completes before actionable downstream work; only a no-work terminal route (`clarification-only` or `direct-response`) may defer it because no work begins. If an undecided entry is known, the runtime must surface `merge-companion` first, but a user-explicit defer/skip may continue with that entry still undecided.

The user-input governance graph is ordered by exact session intake, then task-router, then tool-stage tool-checkpoint. A known pending-merge result remains outside and before that graph; a missing manual result is a pre-work requirement rather than a reason to inspect state during a no-work clarification. `tool-checkpoint` is a PreToolUse/BeforeTool checkpoint, not part of user-input intake order. When surfaced, the visible control schema must include `hook-stage: PreToolUse` for Claude/Codex and `meaning: tool-call retry checkpoint, not user-input intake`.

1. User input triggers `session-intent-analyzer` on every turn. The hook atomically writes the input digest, session ledger, ordered event and database discovery metadata, then allows the agent turn to continue.
2. task-router and report-only skill-evolution consume the same exact session through the authoritative storage API.
3. Session-intent intake feeds task-router directly. The same exact session supplies the report-only skill-evolution terminal branch, which does not feed task-router. Task-router owns routing and current work selection, not intake, raw intent inference, credentials, or native tool permission. Legacy model judgment fields/events remain history; no classifier prerequisite or permission derivation is generated.
4. The task-router reminder requires exact session-intent preflight and a usable current input anchor. Missing identity or degraded recording receives truthful recovery guidance. Session-intent intake feeds task-router directly. The same exact session supplies the report-only skill-evolution terminal branch, which does not feed task-router. Task-router owns routing and current work selection, not intake, raw intent inference, credentials, or native tool permission.
5. tool-checkpoint is a PreToolUse/BeforeTool checkpoint, not user-input intake. It deduplicates routine surface by exact input lineage and preserves strict audit logging. It does not decide native tool permission from legacy model judgments, tool-call identity, payload content, or audit metadata. Native tool permission stays with its host; human authority, credentials, and provenance remain applicable.

## Dynamic Focus Contract

Dynamic focus control is part of the session gate contract. Work is split into semantic atoms, but the scope of attention is not fixed and does not expand in only one direction. User interaction, mismatch location, verification burden, and recovery cost can move the current focus across micro, meso, macro, and meta layers.

- micro: a tool call, command result, format check, or single semantic atom
- meso: a sub-task, boundary-contract surface, or local source-target mapping
- macro: integrated output, SSOT alignment, user constraint alignment, or cross-document logic
- meta: task necessity, task definition, scope expansion, or premise validity

When mismatch appears, the runtime procedure reopens the smallest layer that contains the cause. If the larger premise or integrated logic is wrong, macro or meta is repaired. If the atomic output or local sub-task is wrong, micro or meso is repaired. `calls` remains static and sparse. A checkpoint runs only while a live uncertainty exists and its possible outcomes can change the next decision. Stop the sequence when a checkpoint produces no relevant state delta or when further checking displaces the primary objective.

## Routing Surface Contract

`task-router` owns the reusable work judgment for the current turn. After it reads exact session intent context, it emits `routing-surface`. `session-intent-analyzer` records semantic facts and accumulated decisions, not display recommendations. The governance surface policy consumes `routing-surface`; it must not recompute a competing task-complexity scale. Stable contract phrase: task-router owns the reusable work judgment; session-intent-analyzer records semantic facts and accumulated decisions; governance surface policy consumes routing-surface.

`routing-surface` reuses existing signals: `change-depth` uses `minimal | localized | structural | systemic`, `focus-layer` uses `micro | meso | macro | meta`, and `verification-complexity` maps to task-complexity-level-1 through task-complexity-level-3. If a value is unknown, ambiguous, or contradicts later evidence, consumers fail closed to fuller surface and reopen focus through the existing scope reopen point.

It also preserves `primary-request`, `causal-axis`, and `response-order` from the accepted session intent. The user's terminal objective outranks investigative means. Treat investigation, provenance reconstruction, artifact preservation, and worktree inspection as means unless the user explicitly requests one as a deliverable. Do not let a means replace, narrow, or expand the terminal objective.

`response-mode: clarification-only` is a terminal route for a missing essential referent or decisive input when the current conversation supports neither an answer nor a safe action. Intake and routing still run internally. Ask only for the minimum decisive information. Do not inspect files, repositories, manifests, tools, credentials, or external state to guess the context. Defer a missing manual pending-merge check until the next actionable turn. Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`; strict hook logging remains active. This route is invalid when the content already resolves the question, the user requested a lookup or status check, or a bounded answer can be given with an explicit assumption.

`response-mode: direct-response` is a no-work terminal route for resolved content that needs no file change, side effect, current-state lookup, tool call, or fresh verification. Use `response-order: resolved-intent-first`. Route classification precedes evidence planning. A causal premise is not an inspection request, and verification burden cannot create a current-state referent. Only an explicit inspection request or an established conversational referent authorizes local diagnosis. An established referent authorizes inspection but does not require it. Accept an explicit correction or non-goal before any follow-up, keep the terminal objective above superseded means, and answer a general explanation or stable low-risk how-to without inspecting the current repository or machine. Ambient working directory, opened project, and available tools are not user-provided referents or inspection authority. Treat a technical state named in a general why or how question as the explanation topic, not as evidence about the active workspace. Do not validate or rebut that premise before explaining. First-person, past-cause, deictic wording, tense, technical-state language, ambient context, and tool availability do not bind the question to the workspace; only an identified workspace, supplied workspace evidence, or an explicit request for exact diagnosis or inspection does. Intake and routing still run internally; strict hook logging remains active. Defer a missing manual pending-merge check, load no downstream skill, and emit only the resolved content with at most one decision-relevant caveat. Do not emit `[routing-surface]`, `[task-router]`, `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, or `[io-trace]`. Current or version-specific facts, support or regression claims, high-risk advice, lookup, inspection, modification, and verification requests use a normal route.

An already agreed direction is an input fact, not the goal of this surface. `accepted-continuation` is valid only when session-intent facts contain a recorded prior proposal and recorded acceptance, such as an active decision or acceptance criterion. It can reduce repeated re-explanation of the agreed direction, but it does not lower the verification-complexity level required for the output.

Token reduction is a secondary consequence, not a success metric. A smaller screen surface is valid only when the required gates, verification, forced-risk output, and strict-grade logs remain intact. Stable contract phrase: token reduction is a secondary consequence, not a success metric.

## Work-Impact Projection Contract

Work-Impact Projection classifies hook-internal values by whether they change the next work decision. A value matters when it can alter the work boundary, focus layer, verification burden, or recovery path. Stable contract phrase: change the work boundary, focus layer, verification burden, or recovery.

- Hook execution and the strict audit log are never reduced.
- `agent_visibility.profile` selects user-screen verbosity. It does not gate hook execution, strict logging, or work-impact classification.
- Forced/risk/gate values and failed verification always break through as user surface forced and model hint full.
- Routine/debug values stay full in the strict log, but they are omitted from model hints unless they change focus, boundary, verification, or recovery.
- Low-usefulness suspects such as duplicate reminders, clean-pass status, noop audit rows, debug counters, correlation ids, and historical wording are `routine` or `audit-only` by default. Promote them only when they change the active boundary, focus target, verification burden, or recovery action.
- Unknown, ambiguous, or failed values fail closed to fuller surface and reopen focus through the existing scope-reopen path.
- Goal: hook values should drive focus, boundary, verification, and recovery only when they can change task quality. Token reduction is a secondary consequence, not a success metric. Stable contract phrase: Token reduction is a consequence, not a success metric.

## Runtime Checkpoints

Ghost-ALICE OS documents use an English canonical narrative + English control surface as the default coordination contract. The reader-facing documentation tree also keeps paired Korean counterparts where the tree exposes a pair. Field names, enum values, literal tokens, gate schemas, and allowed/forbidden values stay English and are not translated.

Active work continuation: before choosing a no-work terminal route, reconcile the current input with unfinished authorized work. A status question, explanation request, or correction normally receives a brief commentary answer followed by the supported work. Respect an explicit stop, pause, cancellation, or incompatible replacement; do not infer authority from ambient context or an unresolved ledger flag alone. A fixed branch-local limit blocks its dependent work only. Recording conduct feedback or verifying a report does not satisfy a requested implementation criterion. When the user authorizes a correction, implement it through the owning workflow and test the original failure.

The first commentary must include this block for every normal route; no-work terminal routes emit no control block:

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: <each skill actually called this turn, as name (this turn)> | n/a
- next-required: <skill-name|none>
```

When a final response claims executed work is complete, fixed, successful, or freshly verified, include this block:

```text
[completion-check]
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

`acceptance-criteria` are verifiable criteria extracted from user intent, locked decisions, and boundary-contract. `claim-evidence-map` links each closure claim to a criterion and fresh evidence. If `unverified` is not `none`, do not speak as though completion or success is settled; report the partial state and remaining verification. A finalized `[completion-check]` uses `verdict: pass | fail` and `unverified: none`; an unverified item means you are not finalizing, so report partial state in prose rather than emitting `[completion-check]`. Installed Stop completion hooks require `[completion-check]` for executed-work closure claims and allow routine non-closure responses.

Every user input reopens routing; it does not by itself invalidate unchanged evidence or require reverification. Explaining unchanged prior work is not a new closure claim. Reverify when the relevant state, artifact, or criterion changed; a new error, mismatch, contradiction, or instability appeared; or the user explicitly requested a new check. A change is an observed mutation event, not a possibility, a storage location, or a new user message.

Before running a check, name the live uncertainty and the next decision that each possible outcome can change. If no possible outcome can change the criterion or next decision, do not run the check. Verification output does not create a new obligation to verify the verification.

Before re-reading an artifact to support a claim, apply verify-or-reuse: reuse retained evidence unless an observed trigger exists. Answer an objection that a check was redundant from the recorded mutation authority and events, not from hypothetical external changes. Say each fact, status, plan, and apology once; later messages carry only new results or decisions. Contract: `coding-convention/verification-before-completion/references/verify-or-reuse.md`, shared unchanged by Claude and Codex; platform adapters map tool names only.

When you confirm a flaw in a prompt or other deliverable you provided, return the corrected complete deliverable unless the user asked only for diagnosis (`confirmed-flaw-not-propagated`).

Hard sequence for a new current-turn closure claim: skill load/call -> decision-relevant fresh verification -> [completion-check]. A successful result already returned in this turn, such as a write or a test run, is that fresh verification; the order places the skill before the claim and does not require repeating a successful check. Before making that claim, load or call `verification-before-completion` for the current turn, run and read the decision-relevant fresh verification, and only then write `[completion-check]`. If any step is missing or out of order, the completion-check is invalid.

The `skill-call:` line records that the workflow for that skill was actually executed through the platform's skill execution mechanism in this turn. On Claude Code, write it only after a visible Skill call. On Codex, where no visible Skill tool exists, write it only after using a fresh or valid retained instruction body and following the workflow this turn.

`verification-before-completion` is the lifecycle gate before executed-work closure claims. Routine explanations, meta-discussion, and options do not require it unless they claim finished work or verified results. On Claude Code, `[completion-check]` carries no `skill-call: verification-before-completion (this turn)` line: the Stop hook verifies the visible Skill call from the transcript. On Codex, where no visible Skill surface exists, write that line in `[completion-check]` only when a fresh or valid retained instruction body was used and the workflow followed in this turn.

Printed `[io-trace]` is optional and cannot gate completion by its presence, order or skill-list spelling. Preserve strict internal audit and applicable verification: Claude checks the actual current-turn Skill call; Codex requires the exact verification skill self-report after its workflow is performed. Recovery uses Core delivery selection: only usable visible prior payload with unchanged claims permits metadata-only repair. Final-only, unknown or missing usable prior payload requires one complete assembled answer; changed claims require a corrected complete answer. Withdraw unsupported claims honestly and retain original proof/time without unchanged business reverification. Claude retains its one-retry budget and visible unresolved-defect notice; Codex continues to block genuine completion-evidence defects. The host retains the business payload outside the ledger; the agent assembles the final answer according to the observed delivery surface.

In Codex environments without a visible Skill surface:

- Read or validly reuse the relevant instruction body before marking a required gate done.
- Do not mark a gate complete because of metadata, descriptions, prior verdicts, or "I already know it."
- If neither a fresh nor a valid retained instruction body is available, the gate is pending; do not list a skill-call.
- Apply the same standard to simple tasks, already-routed tasks, and cases where metadata appears sufficient.

## tool-checkpoint Visible Surface

Session-intent intake feeds task-router directly. The same exact session supplies the report-only skill-evolution terminal branch, which does not feed task-router. Task-router owns routing and current work selection, not intake, raw intent inference, credentials, or native tool permission.

The default `[tool-checkpoint]` block carries `intent` and `why`. Add `procedure` when it changes the next work decision or clarifies a non-routine step. Add `contract-ref` and `contract-check` when a boundary-contract is active. Add `localized-human-note`, `rejected-alternatives`, `unverified-premises`, and `failure-mode-if-wrong` only when a side effect, forced signal, mismatch, or meaningful user decision point makes those fields useful. This preserves the checkpoint procedure without forcing low-impact tool calls to repeat values that do not change the work boundary, focus layer, verification burden, or recovery. Add `verify-or-reuse` when a call re-reads an artifact to support a claim, with `why` naming the observed trigger. This field is model-side reasoning; it does not grant native tool permission.

`recovery-action` is conditional. Add it only when the failure mode requires a concrete recovery step, scope reopen, external side effect handling, or another hard-to-recover action. Keep it as a stable English action phrase or slug. If mismatch changes scope, state the `focus-layer` and `scope-reopen` target in `procedure` or `recovery-action`.

Do not require separate `recovery-cost` or `recovery-note` fields in routine tool checkpoints. Recovery cost belongs to work-impact projection, verification planning, or high-impact exception handling, not every PreToolUse message. Human-facing recovery explanation belongs in `localized-human-note` or the conditional `recovery-action` only when it changes the operator's next decision.

## tool-checkpoint Batch / Continuation Compression

Runtime hooks surface one full `[tool-checkpoint]` per session input lineage and keep checking later tool calls silently in that same lineage.

`[tool-checkpoint:batch]` and `[tool-checkpoint:continuation]` are compact forms to reduce repeated full-gate cost. They do not create new permission and do not infer that a new tool action is safe.

`[tool-checkpoint:continuation]` refers only to output polling for the same process/session/tool-call id. A new user input, native denial, mismatch, or other state change returns to a surfaced `[tool-checkpoint]`.

Simple polling of the same ref is not a duty to repeat output forever; the compact form avoids repeating the full gate. Expose it on the first poll in the input lineage or when state changes.

## Notes

- Treat recommendations as claims that require verification, not as casual opinions.
- Reuse existing evidence when the relevant state and criterion are unchanged; state its age and do not relabel it as current-turn verification.
- Reapply `task-router` for each turn.
- `task-router` decides only whether `boundary-contract` is required. `boundary-contract` owns allowed-surface, file names, and test-purpose.
- If task-router outputs `boundary-contract: required`, the next required gate is boundary-contract.
- The `skill-call:` field structurally prevents mixing form completion with actual obligation completion. It was introduced after observing that prose-only reminders still allowed omissions within the same turn.
- Metadata-only skill matching is candidate discovery, not execution. Required gate skills are not complete unless a fresh or valid retained instruction body was used and the workflow followed.

Instruction-body loading follows `coding-convention/verification-before-completion/references/verify-or-reuse.md`, section Instruction Body Reuse (installed path on Codex). A retained instruction body may be reused with a same-session coded load record; current-input workflow judgments still run. Retain actual reads and valid reuse in internal execution evidence; distinguish them in any requested trace. Routine clean merge results need no repeated prose; preserve pending/changed/failed-state warnings.

## Managed Completion Recording Contract

Core projects only current-ready-owned execution units from the accepted response route, owner and work-unit/contract revision. AP v2 records unit-qualified admitted criteria; historical cumulative criteria and unadmitted protections are not execution obligations. The managed registered no-argument hook chain selects `autopilot-completion-receipt.v2`; public legacy APIs retain their v1 default. Protocol/location transport grants no session, origin, native trust, admission or approval. Core source APIs, runtime modules, ledger authority, project, installed skills and platform state have separate explicit location bindings; a normal upgrade preserves its authoritative ledger.

The current publication request can bind compatible accepted original proof without changing its source/time. A new input alone does not reopen unchanged business verification. Applicability is Core-owned; availability is AP-owned. Recording-only failure keeps intake and binding-repair tools usable and blocks publication/application until repaired, while independent authorized work continues. An actual criterion or relevant artifact/owner/target change reopens only affected coverage. Core response delivery allows metadata-only repair only with a usable visible prior answer and unchanged claims; other surfaces require a complete assembled payload, and missing proof requires truthful claim correction. Printed io-trace is optional; strict audit remains required.

## Current Selection and Bounded Recovery

Core reconciles the current request with continuing authorized work before selecting execution/nonexecution. A bounded discussion with no currently selected execution has no AP publication; an actually executed read-only review/report or positive closure still follows owning verification. Core owns the reusable routing projection and current `response-route.v1` selection. Domain owners retain their criterion checks; a selector cannot waive them.

When AP is enabled, it consumes only the actual current ready qualified selected execution and Core's `publication_route: none | prepare | publish | blocked`. Run existence and accumulated backlog are insufficient. Missing/stale route blocks dependent capture or advancement, not existing authorization or native tool permission. New input rebinds the request; unchanged valid proof keeps its original source and time. Relevant criterion, artifact or dependency changes reopen only affected proof. Per-criterion decisions remain distinct from aggregate publication and host delivery; aggregate decisions never rerun all checks.

A first recording-only failure permits bounded repair. An identical unchanged failure parks recording until a decision-relevant recording/dependency change; independent authorized work continues. Supported business outcome, recording/application, whole-unit completion and delivery scope remain separate. Never guess a substitute root, source, receipt, approval or target to repair recording.

Owners may reopen an artifact, mapping, composition, route or inferred premise in the same approved work and return to useful production. Micro, meso, macro and meta are bidirectional focus levels, not an ascending ritual. Ask only for an exact human-owned decision or changed explicit authority; existing authorization needs no automatic reapproval. Evolution remains a report-only terminal branch and cannot automatically enter production.
