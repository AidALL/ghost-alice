---
name: boundary-contract
description: "Use immediately after task-router routes boundary-contract: required. Before modification, implementation, or verification, declare objective, non-goals, allowed/prohibited surfaces, and stop conditions as a contract; do not execute the work."
compatibility:
  - "Python 3.11+ standard library"
---

# Boundary Contract

boundary-contract declares the work boundary before implementation,
modification, or verification. It does not perform the work itself. Its only job
is to state which surfaces are allowed, which surfaces are prohibited, what
counts as success, and when the agent must stop.
## Contents

- [When To Use](#when-to-use)
- [Language Contract](#language-contract)
- [Core Rules](#core-rules)
- [Output Schema](#output-schema)
- [Phase Rules](#phase-rules)
  - [discovery](#discovery)
  - [execution](#execution)
  - [verification](#verification)
- [Source Rules](#source-rules)
- [Tool-Checkpoint Link](#tool-checkpoint-link)
- [Failure Modes](#failure-modes)


## When To Use

Use this skill immediately when task-router outputs
`boundary-contract: required`. task-router decides only whether a boundary is
required, and it does not write `allowed-surface`, filenames, or test-purpose.
This skill owns `allowed-surface`, filenames, phase, test purpose, and stop
conditions.

## Language Contract

Use the current user language for human-readable explanations. Keep field
names, enum values, literal tokens, gate schemas, and allowed/forbidden control
values in English.

Stable contract phrase: Keep field names in English.

## Core Rules

- Do not modify code or documents while producing the contract.
- Do not guess filenames.
- Do not invent `allowed-surface` entries without evidence.
- If the target surface is unclear, use `phase: discovery`.
- Separate explicit user instructions from inferred decisions.
- Extract verifiable `acceptance-criteria` from the user intent and locked
  decisions.
- Attach a `source` to every criterion.
- Do not present `inferred` criteria as user-explicit instructions.
- Classify tests by the risk they reduce, not by whether they feel convenient.
- Always include `stop-conditions`.
- Do not perform execution under a discovery contract.
- If later work needs a surface outside the contract, stop and renew the
  contract first.
- Do not narrow the committed objective or `allowed-surface` by your own
  judgment. Covering only a subset ("key spots only", "a representative
  sample", "the important ones") when the user committed the full surface is a
  scope-narrowing violation, the mirror image of editing outside the contract.
- Scope changes need renegotiation in both directions. Widening requires
  stop-and-renew; narrowing requires stop-and-confirm with the user. Never
  shrink or grow the committed scope silently from the agent's own guess.
- Partial coverage is partial status, not completion. If the full committed
  scope is not finished, report what remains and do not speak as though the
  objective is met.
- When the objective is a full set (every file, every occurrence, all targets),
  record a coverage `acceptance-criteria` (an explicit count or enumerated
  list). Completion requires the whole set, not a sample.

## Output Schema

```text
[boundary-contract]
- id: <stable-id>
- phase: discovery | execution | verification
- objective: <work-objective>
- explicit-non-goals:
  - <non-goal>
- allowed-surface:
  - <allowed-read-or-write-surface>
- prohibited-surface:
  - <prohibited-file-layer-or-action>
- locked-decisions:
  - <locked-decision> [source: user-explicit | inferred | previous-tool | system-doc | unknown]
- acceptance-criteria:
  - <criterion-id>: <verifiable-condition> [source: user-explicit | inferred | previous-tool | system-doc]
- open-questions:
  - <non-blocking-open-question>
- test-purpose: none | smoke | regression | contract | domain-rule | visual | integration
- stop-conditions:
  - <condition-that-stops-work>
- next-allowed-actions:
  - <allowed-next-action>
```

## Phase Rules

### discovery

Use discovery when filenames, modules, or verification methods are not yet known.
Only read-only discovery is allowed. Describe action surfaces such as "inspect
project structure" or "identify UI directory candidates"; do not name files
unless they have already been established.

### execution

Use execution after discovery identifies the actual files, modules, or contracts
to change. Narrow `allowed-surface` to concrete files or directories. If an
edit outside the list becomes necessary, stop and renew the contract.

### verification

Use verification after implementation. Separate verification commands,
screenshots, visual smoke checks, and prohibited new edits. Keep the original
`acceptance-criteria` so completion can map claims to evidence.

## Source Rules

- `user-explicit`: The user directly stated the decision.
- `inferred`: The agent is locking a conservative assumption for this phase.
- `previous-tool`: A tool result produced the decision.
- `system-doc`: AGENTS.md, SKILL.md, or policy docs require it.
- `unknown`: The source is unclear. Do not use it as an execution-phase lock.

## Tool-Checkpoint Link

When a boundary contract is active, each tool checkpoint must include:

```text
- contract-ref: <boundary-contract-id>
- contract-check: <allowed-surface-item | next-allowed-action>
```

If `contract-check` does not map to the contract, do not make the tool call.
Renew the contract or ask the user.

## Failure Modes

- task-router writes `allowed-surface` directly.
- A discovery contract guesses filenames.
- An `inferred` decision is presented as user-explicit.
- Verification proceeds without `acceptance-criteria`.
- `[completion-check]` omits `claim-evidence-map`.
- Peripheral evidence is treated as direct evidence for a user criterion.
- Work starts without stop conditions.
- A file outside the contract is changed because the edit seems small.
- New implementation is added during verification.
- The agent covers only part of the committed `allowed-surface` ("key spots
  only", "a representative sample") and speaks as if the objective is complete.
- The committed scope is shrunk silently instead of stopping to confirm, the
  mirror image of changing a file outside the contract.
- A full-set objective ("all files", "every occurrence") is reported complete
  after only a subset is done, with no coverage criterion to measure against.
