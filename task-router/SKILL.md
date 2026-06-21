---
name: task-router
description: "Runs after session-intent-analyzer and jailbreak-detector/downstream gate as their consumer. Decomposes the request and routes output, verification, lifecycle, and boundary skills without owning intake, raw intent inference, or tool permission."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

<SUBAGENT-STOP>
If this agent was dispatched to perform only a specific subtask, skip this
skill.
</SUBAGENT-STOP>

<ROLE-SCOPE>
task-router is the request-routing gate. Its core responsibilities are request
decomposition, work placement, skill routing, and boundary-skill selection based
on already established session intent context.

Language contract: English canonical narrative + English control surface.

Stable contract phrases:
- request decomposition, work placement, skill routing
- raw user intent inference
- current-lineage block gate
- pending merge remains undecided when deferred

task-router is a consumer of session-intent-analyzer and
jailbreak-detector/downstream gate context. It does not own user-input intake,
raw intent inference, ledger updates, accumulated intent storage, jailbreak
decisions, downstream gate state, or tool permission.

This is a routing decision only. task-router does not perform raw user intent
inference and does not decide tool permission.

task-router starts after session-intent preflight when jailbreak-detector has
not recorded a current-lineage block for the current input. Missing
`downstream-gates.json` is `silent allow` when no current-lineage block exists.
An explicit allow gate may be used as release evidence. A current-lineage block
gate pauses task-router and downstream work.

task-router is not a tool permission owner and not a tool-checkpoint owner.
Tool execution permission, full `[tool-checkpoint]` schema, tool-stage decision
policy, and downstream gate state belong to runtime hooks and dedicated skills.
`tool-checkpoint` is a tool-stage `PreToolUse`/`BeforeTool` checkpoint, not
user-input intake.

Stable checkpoint phrases: `hook-stage: PreToolUse` and
`meaning: tool-call retry checkpoint, not user-input intake`.
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

task-router scans available skill descriptions against the current session
intent context and the current request surface. It records which output,
verification, lifecycle, and boundary skills apply before downstream work
begins.
## Contents

- [Routing Contract](#routing-contract)
- [1. Procedure](#1-procedure)
  - [1.0 Pending-Merge Precheck](#10-pending-merge-precheck)
  - [1.1 Consume Session Intent Context](#11-consume-session-intent-context)
  - [1.1.1 Routing Surface](#111-routing-surface)
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

The normative routing contract is the `<ROUTING-CONTRACT>` block above. This
section is an index pointer, not a second source of truth.

Stable index phrase: after session-intent-analyzer intake and
jailbreak-detector/downstream gate opportunity.

## 1. Procedure

### 1.0 Pending-Merge Precheck

At task-router start, before consuming session intent context:

1. Identify the platform.
2. If hook evidence already reports current platform pending-merge status, use
   it.
3. If the hook reports undecided entries, surface merge-companion first.
4. If the hook reports `hook-verified clean`, do not repeat shell checks.
5. In hookless/manual mode, inspect
   `~/.ghost-alice/pending-merges/<platform>/manifest.json`.
6. If undecided entries exist, surface merge-companion. If the user explicitly
   defers or skips, `user-explicit defer/skip may continue`; the `pending merge
   remains undecided when deferred`. Missing, empty, or parse-failing manifest is
   silent clean pass.

### 1.1 Consume Session Intent Context

Use the current intent summary and downstream gate context first. The raw user
input is not the source of truth. Surface signals may supplement missing
context.

This step performs atomic meaning decomposition from the accepted session
intent context.

Extract:

- action category: create, analyze, edit, verify, lookup, or other
- domain: development, docs, research, operations, cross-cutting, or other
- output shape: code, document, report, registration, none
- input file type: PDF, DOCX, CSV, etc.
- verification signal: fact-check, consistency, schema, regulation, visual, etc.
- boundary signal: explicit non-goals, prohibited layers, read-only discovery,
  screenshot-only checks, or unclear file surface
- change-depth signal: minimal, localized, structural, systemic

### 1.1.1 Routing Surface

task-router emits a reusable `routing-surface` after atomic meaning
decomposition. This is the single reusable work judgment for downstream
boundary, verification, lifecycle, and governance surface consumers.

Use this format:

```text
[routing-surface]
- intent-relation: new | continuation | accepted-continuation | changed | correction | ambiguous
- change-depth: minimal | localized | structural | systemic
- focus-layer: micro | meso | macro | meta
- verification-complexity: level-1 | level-2 | level-3
- boundary-contract: required | n/a
- forced-visibility: yes | no
- reason: <short semantic reason>
```

Rules:

- Stable contract phrase: accepted-continuation requires recorded acceptance;
  unknown routing-surface values fail closed.
- `change-depth` reuses the Sufficient Change Principle below.
- `focus-layer` reuses the Dynamic Focus contract from the session gate matrix.
- `verification-complexity` maps to the existing task-complexity levels.
- `accepted-continuation` requires recorded acceptance in session-intent facts,
  such as an active decision or acceptance criterion. Do not infer it from a
  phrase alone.
- unknown routing-surface values fail closed: consumers show full surface and
  reopen focus instead of compacting.
- session-intent-analyzer records semantic facts and accumulated decisions;
  task-router owns this reusable work judgment.
- `routing-surface` does not decide tool permission and does not suppress any
  required gate.

### Sufficient Change Principle

Do not treat minimal patch as a golden rule. Classify the problem cause,
structure, and impact surface before choosing `sufficient-change-depth`.

- Use `minimal` only when the cause is local and recovery cost is small.
- Use `localized`, `structural`, or `systemic` when the request involves
  open-source hardening, compatibility, governance rules, repeated failure, or
  cross-surface consistency.
- When competing change sets conflict, prefer the one that satisfies the locked
  contract and survives targeted tests; do not choose by recency, authorship, or
  smaller diff alone.
- Temporary patch work is allowed only when the user explicitly asks for urgent
  recovery; record residual impact.

### 1.2 Match Skills

Scan all loaded skill descriptions and compare them with the extracted routing
input.

Output skills:

- Match by action category, domain, file type, and trigger keywords.
- Include candidates when there is at least a small plausible fit.

Verification skills:

- Use adversarial-verification for evidence consistency, fact-checking,
  numeric claims, legal/patent/grant/IR claims, or source-heavy document review.

Lifecycle skills:

- Use necessity-gate when defining new work, new files, new audit cycles, or
  follow-ups.
- Use verification-before-completion before any completion, success, choice, or
  recommendation claim.
- Use using-coding-convention at the start of development work.

Lifecycle skills are registered to be invoked when their phase is reached, not
invoked immediately. necessity-gate is the exception: it fires immediately at
the work-definition point.

Boundary skill:

Set `boundary-contract: required` if any condition is true:

- implementation, modification, or verification work is requested
- explicit prohibited surfaces exist
- auth, API, DI, navigation, dependency, config, schema, or external side
  effect layers are involved
- read-only discovery is needed because target files or tests are unclear
- screenshot, visual smoke, or read-only checks are tied to scope limits

task-router writes only `boundary-contract: required | n/a` and the reason. It
does not write filenames, `allowed-surface`, `test-purpose`, or tool
permission.

### 1.3 Routing Record

Use this format:

```text
[task-router]
domain: <identified domain>
output-skills: <skill + reason>
verification-skills: <skill + reason>
lifecycle: <registered skills>
boundary-contract: required | n/a
boundary-reason: <why, if required>
next-required: boundary-contract | <skill-name|none>
```

After routing, emit:

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

In Codex, `skill-call` means the relevant `SKILL.md` body was actually read and
followed in the current turn.

### 1.4 Execute Routed Workflow

- If `boundary-contract: required`, run boundary-contract before file discovery,
  output skills, or edits.
- If development work is routed and boundary-contract is required, run
  boundary-contract first, then using-coding-convention.
- If boundary-contract is not required for development work, run
  using-coding-convention immediately.
- Invoke output skills for production work.
- Invoke verification skills after outputs are produced.
- Invoke lifecycle skills when their phase is reached.

## 2. No Skill Match

If no output skill matches, continue without one. task-router does not block the
work. verification-before-completion still applies before completion claims.

## 3. Relationship To using-coding-convention

task-router performs repository-wide first-pass routing. using-coding-convention
performs second-pass routing inside the coding-convention skill family.

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
- task-router treats absent `downstream-gates.json` as denial when no
  current-lineage block exists.
- task-router writes `allowed-surface` instead of handing off to
  boundary-contract.
- The agent opens files before routing.
- Verification skills are deferred until after the work is already claimed
  complete.
- A previous turn's routing is reused without current-turn routing.
