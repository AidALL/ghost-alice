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
  - [1.1.1 Routing Surface](#111-routing-surface)
  - [1.1.2 Clarification-Only Terminal Route](#112-clarification-only-terminal-route)
  - [1.1.3 Direct-Response Terminal Route](#113-direct-response-terminal-route)
  - [Sufficient Change Principle](#sufficient-change-principle)
  - [1.2 Match Skills](#12-match-skills)
  - [1.3 Routing Record](#13-routing-record)
  - [1.4 Execute Routed Workflow](#14-execute-routed-workflow)
- [2. No Skill Match](#2-no-skill-match)
- [3. Relationship To using-coding-convention](#3-relationship-to-using-coding-convention)
- [4. Examples](#4-examples)
  - [Operations](#operations)
  - [Document Verification](#document-verification)
  - [Development](#development)
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

This step performs atomic meaning decomposition from the accepted session intent context.

Identify the user's primary request, whether a question or instruction, before adjacent detail. Preserve a causal axis only when the request asks about a cause or relationship; do not invent one for an imperative request. At pre-tool routing, do not fabricate or require an unsupported answer. Preserve the request for downstream output, which leads with the supported causal answer or completed imperative result after the necessary evidence or work.

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
- Route classification precedes evidence planning. A premise or symptom embedded in a causal question is not itself an inspection or verification request. Classify a request as current-state lookup only when the user explicitly asks to inspect, verify, or determine the exact local cause, or when the current conversation already establishes a specific repository, session, machine, file, or artifact as the referent. First-person wording, tense, technical-state language, ambient working directory, opened project, and tool availability do not establish that referent. When a stable general mechanism answers the question, use `direct-response`; verification rules must not promote it to a normal route.
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

Use `response-mode: clarification-only` only when an essential referent or decisive input is missing, the current conversation does not already supply it, and no supported answer or safe action can begin without it. Intake and routing still run internally; this route terminates before downstream skills or work.

Ask only for the minimum decisive information, normally one concise question. Do not inspect files, repositories, manifests, tools, credentials, or external state to guess what the user meant. Defer a manual pending-merge check until the next actionable turn when no hook result exists. Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`; strict hook logging remains active.

Do not use this route when the content already resolves the question, when existing conversation context supplies the referent, when the user requested a lookup or status check, or merely to avoid work. If a useful bounded answer can be given with an explicit assumption, answer it instead of punting.

### 1.1.3 Direct-Response Terminal Route

Use `response-mode: direct-response` when the current input and conversation fully support the answer and no file change, external side effect, current-state lookup, tool call, or fresh verification is needed. Eligible content includes an explicit correction or non-goal that can be acknowledged immediately, a terminal objective that supersedes a previously proposed means, a bounded explanation of a general mechanism, and stable, low-risk, non-current general guidance.

Ambient working directory, opened project, and available tools are not user-provided referents or inspection authority. Treat a technical state named in a general why or how question as the explanation topic, not as evidence that the active workspace is currently in that state. Do not validate or rebut that premise before explaining. First-person, past-cause, and deictic wording does not bind the question to the active workspace. The workspace becomes the target only when the user identifies it, supplies workspace evidence, or explicitly requests exact diagnosis or inspection. Explain common causes first; offer repository-specific inspection only conditionally when the user asks for the exact cause.

Lead with the resolved content. For a correction, accept it in the first sentence, state the corrected scope or non-goal, and do not revive the superseded direction as a requirement, solution, or verification target. For a causal explanation, answer the general cause and make any repository-specific diagnosis conditional instead of inspecting the current repository. For a direct how-to, give the shortest actionable method that satisfies the stated constraint. Add at most one short caveat when a real unresolved risk could change the answer.

Intake, security review, routing, and strict hook logging still run internally. Defer a missing manual pending-merge check until the next actionable turn. Do not inspect files, repositories, manifests, tools, credentials, or external state, and do not emit `[routing-surface]`, `[task-router]`, `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, or `[io-trace]` on the user surface. Do not load downstream skills or create a boundary contract.

This route is unavailable when the user requests a lookup, verification, file or state inspection, modification, external side effect, current or version-specific fact, support or regression judgment, or high-risk advice. Any required tool call or fresh evidence reclassifies the turn as `normal`.

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
- skill-call: session-intent-analyzer (this turn); task-router (this turn); using-coding-convention (this turn) | n/a
- next-required: <skill-name|none>
```

In Codex, `skill-call` means the relevant `SKILL.md` body was actually read and followed in the current turn.

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

## 4. Examples

### Operations

```text
request: "Schedule a meeting tomorrow at 3."
[task-router]
domain: operations
output-skills: none
verification-skills: none
lifecycle: verification-before-completion
boundary-contract: n/a
next-required: none
```

### Clarification Only

```text
request: "I can't get it to work. How do I fix this?"
[task-router]
domain: other
response-mode: clarification-only
output-skills: none
verification-skills: none
lifecycle: none
boundary-contract: n/a
next-required: user-input
```

The user-facing response asks for the exact error and relevant context without inspecting the working directory or exposing governance ceremony.

### Direct Response

```text
request: "How can I compile in Eclipse without running the program?"
[task-router]
domain: development / tooling
response-mode: direct-response
output-skills: none
verification-skills: none
lifecycle: none
boundary-contract: n/a
next-required: user-input
```

The user-facing response gives the compile-only action directly and omits routing, verification, and audit ceremony.

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

### Document Verification

```text
request: "Check whether the requirements table and body text match."
[task-router]
domain: docs
output-skills: document extraction if available
verification-skills: adversarial-verification
lifecycle: verification-before-completion
boundary-contract: n/a
next-required: text extraction addon if installed; otherwise ask for readable source text
```

### Development

```text
request: "Build only the Android login UI mockup. Do not touch auth, API, DI, or navigation. Verify with screenshot."
[task-router]
domain: development / Android UI
output-skills: development workflow skill if installed
verification-skills: verification-before-completion
lifecycle: using-coding-convention -> verification-before-completion
boundary-contract: required
boundary-reason: modification request with explicit prohibited surfaces and screenshot verification
next-required: boundary-contract
```

## Failure Modes

- task-router runs before session-intent-analyzer.
- task-router treats absent `downstream-gates.json` as denial when no current-lineage block exists.
- task-router writes `allowed-surface` instead of handing off to boundary-contract.
- The agent opens files before routing.
- Verification skills are deferred until after the work is already claimed complete.
- A previous turn's routing is reused without current-turn routing.
- Missing context is used as permission to inspect the working directory, Git state, manifests, credentials, or tools instead of asking the minimum clarification.
- `clarification-only` is used even though the content or current conversation already resolves the question.
- A general explanatory question is silently converted into a diagnosis of the current repository or machine.
- An explicit correction or terminal objective is acknowledged only after defending, investigating, or verifying the superseded direction.
- `direct-response` is used for a current, version-sensitive, high-risk, lookup, inspection, modification, or verification request.
