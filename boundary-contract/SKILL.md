---
name: boundary-contract
description: "Use immediately after task-router routes boundary-contract: required. Before modification, implementation, or verification, declare objective, non-goals, allowed/prohibited surfaces, and stop conditions as a contract; do not execute the work."
compatibility:
  - "Python 3.11+ standard library"
---

# Boundary Contract

boundary-contract declares the work boundary before implementation, modification, or verification. It does not perform the work itself. Its only job is to state which surfaces are allowed, which surfaces are prohibited, what counts as success, and when the agent must stop.
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
- [Exact Protected-File Evidence](#exact-protected-file-evidence)
- [Failure Modes](#failure-modes)


## When To Use

Use this skill immediately when task-router outputs `boundary-contract: required`. task-router decides only whether a boundary is required, and it does not write `allowed-surface`, filenames, or test-purpose. This skill owns `allowed-surface`, filenames, phase, test purpose, and stop conditions.

## Language Contract

Use the current user language for human-readable explanations. Keep field names, enum values, literal tokens, gate schemas, and allowed/forbidden control values in English.

Stable contract phrase: Keep field names in English.

## Core Rules

- Do not modify code or documents while producing the contract.
- Do not guess filenames.
- Do not invent `allowed-surface` entries without evidence.
- If the target surface is unclear, use `phase: discovery`.
- Separate explicit user instructions from inferred decisions.
- Record the terminal objective separately from implementation and investigation means.
- Extract verifiable `acceptance-criteria` from the user intent and locked decisions.
- Acceptance criteria describe the requested outcome, not incidental methods, examples, artifact counts, or verification mechanics unless the user made them requirements.
- Attach a `source` to every criterion.
- Do not present `inferred` criteria as user-explicit instructions.
- Classify tests by the risk they reduce, not by whether they feel convenient.
- Always include `stop-conditions`.
- Do not perform execution under a discovery contract.
- If later work needs a surface outside the contract, stop and renew the contract first.
- Do not narrow the committed objective or `allowed-surface` by your own judgment. Covering only a subset ("key spots only", "a representative sample", "the important ones") when the user committed the full surface is a scope-narrowing violation, the mirror image of editing outside the contract.
- Renew an agent-declared implementation contract before using an additional surface already inside the user authorization envelope. A changed user-owned boundary or committed objective requires the exact human decision. Do not silently narrow the requested outcome or infer new authority from a wider focus.
- Partial coverage is partial status, not completion. If the full committed scope is not finished, report what remains and do not speak as though the objective is met.
- When the objective is a full set (every file, every occurrence, all targets), record a coverage `acceptance-criteria` (an explicit count or enumerated list). Completion requires the whole set, not a sample.

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

Use discovery when filenames, modules, or verification methods are not yet known. Only read-only discovery is allowed. Describe action surfaces such as "inspect project structure" or "identify UI directory candidates"; do not name files unless they have already been established.

### execution

Use execution after discovery identifies the actual files, modules, or contracts to change. Narrow `allowed-surface` to concrete files or directories. If an edit outside the list becomes necessary, stop and renew the contract.

### verification

Use verification after implementation. Separate verification commands, screenshots, visual smoke checks, and prohibited new edits. Keep the original `acceptance-criteria` so completion can map claims to evidence.

## Source Rules

- Assign a source to each atomic proposition. Split combined statements with different origins into separate items; a source label must not carry across mixed origins.
- `user-explicit`: The user directly stated the decision. Actual human approval remains user-explicit.
- `inferred`: The agent is locking a conservative assumption for this phase.
- `previous-tool`: A tool result produced the decision, including observed hook/runtime-generated controls even when transported with `role=user`. A generated approval digest and its preservation requirement have this source; transport role does not turn them into human approval.
- `system-doc`: AGENTS.md, SKILL.md, or policy docs require it.
- `unknown`: The source is unclear. Do not use it as an execution-phase lock.

Keep a user-protected file and a runtime generation separate:

```text
- Preserve protected.txt as the user requested. [source: user-explicit]
- Preserve the hook-provided approval-generation value. [source: previous-tool]
```

## Tool-Checkpoint Link

When a boundary contract is active, each tool checkpoint must include:

```text
- contract-ref: <boundary-contract-id>
- contract-check: <allowed-surface-item | next-allowed-action>
```

If `contract-check` does not map to the implementation contract, keep that call closed while renewing the contract within the existing authorization envelope. Ask only when an exact human-owned boundary or decisive input is missing.

## Exact Protected-File Evidence

When an execution contract protects existing files or requires exact modification-time comparisons, use the installed `scripts/file_guard.py` before dependent writes. It captures file content, exact timestamp, identity and mode without writing task files. Resolve the script relative to this loaded skill directory; read its `--help` only if invocation details are needed.

```text
python3 <loaded-skill-directory>/scripts/file_guard.py capture --path <protected-file> [--path <another-protected-file>]
python3 <loaded-skill-directory>/scripts/file_guard.py check --token <unchanged-token-from-capture>
```

`capture` emits JSON containing a string `token` and its `paths`. Keep that token verbatim in current-session tool state and pass it to `check` using the host's structured arguments or proper shell quoting. Do not decode, regenerate or hand-copy its timestamp fields. Do not send nanosecond timestamps or large identity integers through JavaScript `Number`, JSON numeric fields, floating-point seconds or rounded displays. Do not create a replacement numeric baseline when the helper is available.

Capture once before the protected operation. Check immediately before a dependent write, and after the operation when preservation is an acceptance condition. A failed check stops the dependent write; investigate the original baseline instead of replacing it with the current state. If the installed helper is unavailable, keep capture and comparison in one exact-integer process or persist integer fields as strings from their original source. Never recover lost precision by converting an already rounded number to a string.

The guard observes regular files at a point in time; it is not an atomic write lock, authorization token or business-completion proof. It rejects symlink paths and parent traversal (`..`); use the actual absolute file path. It does not cover directory membership. Preserve any additional contract-specific checks separately. A bookkeeping-only recovery does not itself invalidate a completed guard or business check; do not rerun either without a relevant change or contrary evidence.

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
- The agent covers only part of the committed `allowed-surface` ("key spots only", "a representative sample") and speaks as if the objective is complete.
- The committed scope is shrunk silently instead of stopping to confirm, the mirror image of changing a file outside the contract.
- A full-set objective ("all files", "every occurrence") is reported complete after only a subset is done, with no coverage criterion to measure against.

## Bounded repair and authority

Actual current and prior human instructions supply the authorization envelope: objective, prohibitions, allowed actions and targets, side effects and resources. Retained authority facts preserve their human source; trusted system/project constraints retain their identified source. Intake compresses semantic facts and the router selects current work without granting authority. Distinguish human-owned criteria and resource choices from agent-derived interpretations and bounded local plans; an absent explicit resource cap is not an approved human limit. Renew a narrower implementation contract only inside actual authority, without weakening acceptance criteria or inferring permission from focus expansion. Request an exact human-owned decision before crossing its boundary. Actual grant renews only the affected plan and granted scope; denial parks it; no response leaves it waiting. Continue other eligible authorized work.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.

The owning workflow calls the selected business action or corrective change and receives its actual result; declared action output may itself supply the verification observation. Domain verification returns actual observations, source and time to the workflow for criterion judgment. The applicable registered v2 pretool automatically prepares AP origin/receipt before the tool and returns current qualified command context. Reuse that valid prepared result; use an explicit returned preparation helper only when the applicable dependency remains genuinely unfulfilled. A recording gap affects capture/publication/application recordability; AP pretool continues and native tool permission remains with its host. Required or discriminating business observation follows its own authority and resource bounds; recording recovery rules govern recording attempts only. Preserve valid proof and its original source/time, including when recording is unavailable.

For a candidate closure, the owning workflow obtains or reuses supported proof. Applicable AP publication uses the exact current qualified selection, prepared AP receipt and actual returned publish command to leave a pending-adapter request. Native candidate/final dispatch invokes its actually registered handlers: the completion guard checks candidate fields and applicable skill evidence; the privileged AP Stop adapter revalidates publication, proof, ownership and dependencies. Only accepted current publication reaches the exact Core criterion writer within the shared authority transaction. Missing, stale or invalid publication returns a recording diagnostic without a criterion write. Actual application status and remaining criteria determine unit recording/completion claims. The handlers retain their actual native invocation semantics. Core owns delivery policy and may assemble the permitted payload; the workflow/native host performs visible delivery. Candidate claims use the supported current scope, while actual recording/application and visibility results govern further repair and closure claims. Preserve already-visible unchanged business content for applicable control-only repair. Return an admissible affected step to the workflow, or honest partial/waiting status when no eligible continuation remains.
