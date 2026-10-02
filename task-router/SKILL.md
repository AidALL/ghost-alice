---
name: task-router
description: "Runs after session-intent-analyzer and jailbreak-detector/downstream gate as their consumer. Decomposes the request and routes output, verification, lifecycle, and boundary skills without owning intake, raw intent inference, or tool permission."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

<SUBAGENT-STOP>
If this agent was dispatched to perform only a specific subtask, skip this skill.
</SUBAGENT-STOP>

<ROLE-SCOPE>
task-router is the request-routing gate. Its core responsibilities are request decomposition, work placement, skill routing, and boundary-skill selection based on already established session intent context.

Language contract: English canonical narrative + English control surface.

Stable contract phrases:
- request decomposition, work placement, skill routing
- raw user intent inference
- current-lineage block gate
- pending merge remains undecided when deferred

task-router is a consumer of session-intent-analyzer and jailbreak-detector/downstream gate context. It does not own user-input intake, raw intent inference, ledger updates, accumulated intent storage, jailbreak decisions, downstream gate state, or tool permission.

This is a routing decision only. task-router does not perform raw user intent inference and does not decide tool permission.

task-router starts after session-intent preflight when jailbreak-detector has not recorded a current-lineage block for the current input. Missing `downstream-gates.json` is `silent allow` when no current-lineage block exists. An explicit allow gate may be used as release evidence. A current-lineage block gate pauses task-router and downstream work.

task-router is not a tool permission owner and not a tool-checkpoint owner. Tool execution permission, full `[tool-checkpoint]` schema, tool-stage decision policy, and downstream gate state belong to runtime hooks and dedicated skills. `tool-checkpoint` is a tool-stage `PreToolUse`/`BeforeTool` checkpoint, not user-input intake.

Stable checkpoint phrases: `hook-stage: PreToolUse` and `meaning: tool-call retry checkpoint, not user-input intake`.
</ROLE-SCOPE>

<QUALITY-RATIONALE>
This gate is a quality-maintenance procedure that realigns the goal, the output, the verification, and the boundary skills on every user input. Even for a follow-up request within the same session, a small change in the goal or the constraints can make the previous routing a stale decision.
</QUALITY-RATIONALE>

<ROUTING-CONTRACT>
When there is user input, call this skill after the session-intent-analyzer intake and the jailbreak-detector/downstream gate. This gate is the mandatory starting point for agent-side request decomposition after the session-intent intake, and it runs before any downstream work or tool call. Check its applicability regardless of domain, including coding, documentation, and chores.

This skill never runs before session-intent-analyzer or the jailbreak-detector/downstream gate. The normal order is `pending-merge precheck -> session-intent-analyzer -> jailbreak-detector/downstream-gates -> task-router`. `skill-evolution` is a report-only terminal branch of session-intent-analyzer and is not a path that feeds task-router.

Do not skip it on the agent's own judgment alone, such as "already routed on a previous input", "the same domain", or "a simple follow-up". A subagent-delegated task that is clearly outside task-router's scope follows the `SUBAGENT-STOP` contract.
</ROUTING-CONTRACT>

# task-router

task-router scans available skill descriptions against the current session intent context and the current request surface. It records which output, verification, lifecycle, and boundary skills apply before downstream work begins.
## Contents

- [Routing Contract](#routing-contract)
- [1. Procedure](#1-procedure)
  - [1.0 Pending-Merge Precheck](#10-pending-merge-precheck)
  - [1.1 Consume Session Intent Context](#11-consume-session-intent-context)
  - [Active Work Continuation](#active-work-continuation)
  - [1.1.1 Routing Surface](#111-routing-surface)
  - [1.1.2 Clarification-Only Terminal Route](#112-clarification-only-terminal-route)
  - [1.1.3 Direct-Response Terminal Route](#113-direct-response-terminal-route)
  - [Sufficient Change Principle](#sufficient-change-principle)
  - [1.2 Match Skills](#12-match-skills)
  - [1.3 Routing Record](#13-routing-record)
  - [1.4 Execute Routed Workflow](#14-execute-routed-workflow)
- [2. No Skill Match](#2-no-skill-match)
- [3. Relationship To using-coding-convention](#3-relationship-to-using-coding-convention)
- [4. Reference](#4-reference)
- [Failure Modes](#failure-modes)


## Routing Contract

The normative routing contract is the `<ROUTING-CONTRACT>` block above. This section is an index pointer, not a second source of truth.

Stable index phrase: after session-intent-analyzer intake and jailbreak-detector/downstream gate opportunity.

## 1. Procedure

### 1.0 Pending-Merge Precheck

At task-router start, before consuming session intent context:

1. Identify the platform.
2. If hook evidence already reports current platform pending-merge status, use it.
3. If the hook reports undecided entries, surface merge-companion first.
4. If the hook reports `hook-verified clean`, do not repeat shell checks.
5. If hook evidence is absent, consume only enough accepted intent context to decide whether the turn is a no-work terminal route; do not inspect the manifest first when no actionable work can begin.
6. For any route other than `clarification-only` or `direct-response`, inspect `~/.ghost-alice/pending-merges/<platform>/manifest.json` before downstream work or another tool call.
7. If undecided entries exist, surface merge-companion. If the user explicitly defers or skips, `user-explicit defer/skip may continue`; the `pending merge remains undecided when deferred`. Missing, empty, or parse-failing manifest is silent clean pass.

### 1.1 Consume Session Intent Context

Use the current intent summary and downstream gate context first. The raw user input is not the source of truth. Surface signals may supplement missing context.

Use exact file and executable paths established by runtime, tool, or source evidence rather than deriving directory structure from a skill-family label; when a binding is missing, perform one bounded file inventory and reuse the established path.

This step performs atomic meaning decomposition from the accepted session intent context.

Reconcile the current goal, latest scope, active decisions, accumulated constraints/non-goals, and acceptance criteria before selecting an action category. An accumulated list can retain an earlier turn's restriction. A supported explicit user revision resolves only the named exception; other restrictions remain. Do not silently redefine an edit request as analysis because an older restriction remains in a list, or treat a newer goal as automatic permission. When the context cannot resolve the conflict, identify the conflicting boundary and ask only for that missing decision; proceed with uncontroversial work where possible. Existing explicit authorization that resolves the conflict does not need another approval.

For boundary chronology and conflicting snapshot fields, apply `references/routing-details.md` before choosing the action category.

Identify the user's primary request, whether a question or instruction, before adjacent detail. Preserve a causal axis only when the request asks about a cause or relationship; do not invent one for an imperative request. At pre-tool routing, do not fabricate or require an unsupported answer. Preserve the request for downstream output, which leads with the supported causal answer or completed imperative result after the necessary evidence or work.

When judging a causal or design hypothesis, reason independently about its mechanism and the contrary case or limits; restating the stated goal is not a judgment. When reviewing changes, lead with concrete before-to-after behavior and the evidenced installation or adoption state before utility or necessity advice; scope a finding of no additional need to the evaluated candidate instead of declaring the whole bundle worthless.

Extract:

- primary request: question or instruction
- causal axis: cause or relationship, otherwise `n/a`
- action category: create, analyze, edit, verify, lookup, or other
- domain: development, docs, research, operations, cross-cutting, or other
- output shape: code, document, report, registration, none
- input file type: PDF, DOCX, CSV, etc.
- verification signal: fact-check, consistency, schema, regulation, visual, etc.
- boundary signal: explicit non-goals, prohibited layers, read-only discovery, screenshot-only checks, or unclear file surface
- change-depth signal: minimal, localized, structural, systemic

### Active Work Continuation

Reconcile the current input with unfinished authorized work before selecting a terminal route. A status question, explanation request, or correction during that work normally steers the active task. Answer the question briefly in commentary, apply the correction, and continue the supported implementation or verification. A bounded answer can reuse retained evidence without making the entire turn `direct-response`.

Preserve the attained work stage from retained evidence when answering status or sequence questions. Distinguish feedback recorded, source changed, behavior tested, and runtime installed or adopted; report only supported stages and the remaining gap. These are evidence distinctions, not a mandatory stage ladder. Do not describe an already ongoing behavior improvement as a future first step or restart authorized work to fit a proposed sequence. A status question supplies no new execution permission; continue only within existing authorization.

An explicit stop, pause, cancellation, or incompatible replacement changes that decision. An essential missing decision or an unsafe action can block dependent work; continue independent authorized work where possible. Do not infer unfinished work or authority from the ambient workspace, an unresolved ledger flag alone, or tool availability. Genuine no-work answers keep their terminal route.

A branch-local failure, including a fixed external quota limit or publication bookkeeping conflict, blocks only work that depends on it. Keep the requested result as the completion target, use available supported paths, and report the specific gap. Retry only after an observed change or when a retry can change the next decision.

When a correction identifies an execution failure and the user authorizes fixing it, route to the owning implementation workflow and an original-failure regression. Recording `conduct_feedback` or producing an evolution report is not the correction itself. A report-only recommendation without that authorization remains report-only.

### 1.1.1 Routing Surface

task-router emits a reusable `routing-surface` after atomic meaning decomposition. This is the single reusable work judgment for downstream boundary, verification, lifecycle, and governance surface consumers.

Use this format:

```text
[routing-surface]
- intent-relation: new | continuation | accepted-continuation | changed | correction | ambiguous
- primary-request: <user's primary question or imperative instruction>
- causal-axis: <cause or relationship | n/a>
- response-mode: normal | clarification-only | direct-response
- response-order: causal-answer-first-after-necessary-evidence | imperative-result-first-after-necessary-work | clarification-question-only | resolved-intent-first
- change-depth: minimal | localized | structural | systemic
- focus-layer: micro | meso | macro | meta
- verification-complexity: level-1 | level-2 | level-3
- boundary-contract: required | n/a
- forced-visibility: yes | no
- reason: <short semantic reason>
```

Rules:

- Stable contract phrase: accepted-continuation requires recorded acceptance; unknown routing-surface values fail closed.
- `primary-request` preserves the user's primary question or imperative instruction.
- `response-mode: clarification-only` is the terminal route defined below; ambiguity by itself is not sufficient, and this mode uses `response-order: clarification-question-only`.
- `response-mode: direct-response` is the no-work terminal route for content that can be resolved from the current input and conversation without tools or state access; it uses `response-order: resolved-intent-first`.
- Apply Active Work Continuation first: a brief answer inside ongoing authorized work does not terminate that work.
- Route classification precedes evidence planning. A premise or symptom embedded in a causal question is not itself an inspection or verification request. Classify a request as current-state lookup only when the user explicitly asks to inspect, verify, or determine the exact local cause, or when the current conversation already establishes a specific repository, session, machine, file, or artifact as the referent. An established referent authorizes inspection but does not require it. First-person wording, tense, technical-state language, ambient working directory, opened project, and tool availability do not establish that referent. When a stable general mechanism answers the question, use `direct-response`; verification rules must not promote it to a normal route.
- Before re-reading an artifact to support a claim, apply verify-or-reuse: reuse retained evidence unless an observed trigger exists. The contract lives in `verification-before-completion` under `references/verify-or-reuse.md`.
- The user's terminal objective outranks investigative means.
- Treat investigation, provenance reconstruction, artifact preservation, and worktree inspection as means unless the user explicitly requests one as a deliverable.
- Do not let a means replace, narrow, or expand the terminal objective.
- An explicit correction, non-goal, or terminal objective is decisive content. Missing implementation detail must not replace acknowledgment of that settled content with a clarification-only response. Acknowledge and preserve the settled part first; ask at most one follow-up only for the unresolved part.
- For a causal question, preserve its causal axis. On a normal route, use `response-order: causal-answer-first-after-necessary-evidence`. On a direct-response route, use `response-order: resolved-intent-first` and do not gather evidence.
- For a non-causal imperative request, set `causal-axis: n/a` and use `response-order: imperative-result-first-after-necessary-work`. Do not invent causality.
- `response-order` directs downstream output to lead with the supported answer or completed result, then adjacent detail. It does not require an answer or result before evidence gathering or execution.
- `change-depth` reuses the Sufficient Change Principle below.
- `focus-layer` reuses the Dynamic Focus contract from the session gate matrix.
- `verification-complexity` maps to the existing task-complexity levels.
- `accepted-continuation` requires recorded acceptance in session-intent facts, such as an active decision or acceptance criterion. Do not infer it from a phrase alone.
- unknown routing-surface values fail closed: consumers show full surface and reopen focus instead of compacting.
- session-intent-analyzer records semantic facts and accumulated decisions; task-router owns this reusable work judgment.
- `routing-surface` does not decide tool permission. A no-work terminal route finishes before downstream work gates become applicable; strict intake, security, routing, and hook logging still run.

### 1.1.2 Clarification-Only Terminal Route

Use it only when an essential referent or decisive input is missing and no supported answer or safe action can begin. Ask only for the minimum decisive information, normally one concise question. Intake and routing still run internally. Do not use it when the content already resolves the question, the user requested a lookup or status check, or a bounded answer can be given with an explicit assumption. Do not inspect files, repositories, manifests, tools, credentials, or external state to guess what the user meant, and emit no control block. Full contract: `references/routing-details.md`.

### 1.1.3 Direct-Response Terminal Route

Use it when the current input and conversation fully support the answer with no file change, side effect, current-state lookup, tool call, or fresh verification. Eligible content includes an explicit correction or non-goal, a terminal objective that supersedes a proposed means, a bounded explanation of a general mechanism, stable, low-risk, non-current general guidance, and an answer from retained artifact content when verify-or-reuse yields reuse. Ambient working directory, opened project, and available tools are not user-provided referents or inspection authority. Treat a technical state named in a general why or how question as the explanation topic. Do not validate or rebut that premise before explaining. Lead with the resolved content; Do not inspect files, repositories, manifests, tools, credentials, or external state, and emit no control block. It is unavailable for lookup, inspection, modification, verification, current or version-specific facts, support or regression judgments, and high-risk advice. Full contract: `references/routing-details.md`.

This terminal route also requires that no actionable authorized work remains in the active task, unless the user explicitly stops, pauses, cancels, or replaces it. Use commentary for an answer that interrupts ongoing work.

### Sufficient Change Principle

Do not treat minimal patch as a golden rule. Classify the problem cause, structure, and impact surface before choosing `sufficient-change-depth`.

- Use `minimal` only when the cause is local and recovery cost is small.
- Use `localized`, `structural`, or `systemic` when the request involves open-source hardening, compatibility, governance rules, repeated failure, or cross-surface consistency.
- When competing change sets conflict, prefer the one that satisfies the locked contract and survives targeted tests; do not choose by recency, authorship, or smaller diff alone.
- Temporary patch work is allowed only when the user explicitly asks for urgent recovery; record residual impact.

### 1.2 Match Skills

Scan all loaded skill descriptions and compare them with the extracted routing input.

Output skills:

- Match by action category, domain, file type, and trigger keywords.
- Include candidates when there is at least a small plausible fit.

Verification skills:

- Use adversarial-verification for evidence consistency, fact-checking, numeric claims, legal/patent/grant/IR claims, or source-heavy document review.

Lifecycle skills:

- Use necessity-gate when defining new work, new files, new audit cycles, or follow-ups.
- Use verification-before-completion before any completion, success, choice, or recommendation claim.
- Use using-coding-convention at the start of development work.

Lifecycle skills are registered to be invoked when their phase is reached, not invoked immediately. necessity-gate is the exception: it fires immediately at the work-definition point.

Boundary skill:

Set `boundary-contract: required` if any condition is true:

- implementation, modification, or verification work is requested
- explicit prohibited surfaces exist
- auth, API, DI, navigation, dependency, config, schema, or external side effect layers are involved
- read-only discovery is needed because target files or tests are unclear
- screenshot, visual smoke, or read-only checks are tied to scope limits
- the user corrects the agent for changing, narrowing, widening, relabeling, or substituting the requested objective, scope, selection criterion, priority, or completion meaning and actual modification, inspection, or verification work remains after the correction

task-router writes only `boundary-contract: required | n/a` and the reason. It does not write filenames, `allowed-surface`, `test-purpose`, or tool permission.

### 1.3 Routing Record

Use this format:

```text
[task-router]
domain: <identified domain>
response-mode: normal | clarification-only | direct-response
output-skills: <skill + reason>
verification-skills: <skill + reason>
lifecycle: <registered skills>
boundary-contract: required | n/a
boundary-reason: <why, if required>
next-required: boundary-contract | <skill-name> | user-input | none
```

After a normal route, emit:

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

Instruction-body reuse follows `verification-before-completion/references/verify-or-reuse.md`, section Instruction Body Reuse. In Codex, `skill-call` means a fresh or valid retained instruction body was used and its workflow followed in the current turn.

For `response-mode: clarification-only` or `direct-response`, keep the routing record in the strict audit surface and use the terminal route instead of emitting the user-facing blocks above.

### 1.4 Execute Routed Workflow

- If `boundary-contract: required`, run boundary-contract before file discovery, output skills, or edits.
- If development work is routed and boundary-contract is required, run boundary-contract first, then using-coding-convention.
- If boundary-contract is not required for development work, run using-coding-convention immediately.
- Invoke output skills for production work.
- Invoke verification skills after outputs are produced.
- Invoke lifecycle skills when their phase is reached.

## 2. No Skill Match

If no output skill matches, continue without one. task-router does not block the work. verification-before-completion still applies before completion claims.

## 3. Relationship To using-coding-convention

task-router performs repository-wide first-pass routing. using-coding-convention performs second-pass routing inside the coding-convention skill family.

## 4. Reference

`references/routing-details.md` holds boundary reconciliation, the full clarification-only and direct-response contracts, worked examples, and failure modes. Read it when a route decision is not obvious from the procedure above.

### General Past-Cause Explanation

```text
request: "How did my process end up in this state?"
[task-router]
domain: development / operations
response-mode: direct-response
output-skills: none
verification-skills: none
lifecycle: none
boundary-contract: n/a
next-required: user-input
```

The user-facing response explains common process-state causes first, does not inspect or rebut the premise from the ambient workspace, and offers exact diagnosis only conditionally.

## Failure Modes

- task-router runs before session-intent-analyzer.
- A previous turn's routing is reused without current-turn routing.
- Missing context is used as permission to inspect the working directory, Git state, manifests, credentials, or tools instead of asking the minimum clarification.
- A general explanatory question is silently converted into a diagnosis of the current repository or machine.
- `direct-response` is used for a current, version-sensitive, high-risk, lookup, inspection, modification, or verification request.

The full list is in `references/routing-details.md`.
