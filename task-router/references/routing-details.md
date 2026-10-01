# task-router routing details

Detail moved out of `task-router/SKILL.md` for progressive disclosure. SKILL.md keeps the routing procedure; this file keeps the full terminal-route contracts, boundary reconciliation, worked examples, and failure modes.

## Contents

- [Boundary Reconciliation](#boundary-reconciliation)
- [Clarification-Only Terminal Route](#clarification-only-terminal-route)
- [Direct-Response Terminal Route](#direct-response-terminal-route)
- [Examples](#examples)
- [Failure Modes](#failure-modes)

## Boundary Reconciliation

Do not invent the chronology of a boundary. A timestamp on a decision does not date an undated restriction; active status, admitted criteria, and reports of prior edits alone do not establish that the restriction was later replaced. Distinguish a current user instruction or supplied conversation/event evidence from a compressed current_goal label. A clear current instruction can authorize a bounded change without formal revocation wording. If only conflicting snapshot fields are available, keep that conflict unresolved rather than calling one field initial or outdated without evidence. Ask about the contested authority only; missing task files are a separate issue.

## Clarification-Only Terminal Route

Use `response-mode: clarification-only` only when an essential referent or decisive input is missing, the current conversation does not already supply it, and no supported answer or safe action can begin without it. Intake and routing still run internally; this route terminates before downstream skills or work.

Ask only for the minimum decisive information, normally one concise question. Do not inspect files, repositories, manifests, tools, credentials, or external state to guess what the user meant. Defer a manual pending-merge check until the next actionable turn when no hook result exists. Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`; strict hook logging remains active.

Do not use this route when the content already resolves the question, when existing conversation context supplies the referent, when the user requested a lookup or status check, or merely to avoid work. If a useful bounded answer can be given with an explicit assumption, answer it instead of punting.

## Direct-Response Terminal Route

Apply the Active Work Continuation procedure in `../SKILL.md` first. A status answer or accepted correction within unfinished authorized implementation belongs in commentary, followed by the supported work. Retained evidence can answer the interruption without another check. It does not convert the active task into a no-work terminal response. An explicit stop, pause, cancellation, or incompatible replacement governs continuation; ambient context or an unresolved ledger flag alone cannot authorize work.

Use `response-mode: direct-response` when the current input and conversation fully support the answer and no file change, external side effect, current-state lookup, tool call, or fresh verification is needed. Eligible content includes an explicit correction or non-goal that can be acknowledged immediately, a terminal objective that supersedes a previously proposed means, a bounded explanation of a general mechanism, stable, low-risk, non-current general guidance, and an answer from retained artifact content when verify-or-reuse yields reuse.

Ambient working directory, opened project, and available tools are not user-provided referents or inspection authority. Treat a technical state named in a general why or how question as the explanation topic, not as evidence that the active workspace is currently in that state. Do not validate or rebut that premise before explaining. First-person, past-cause, and deictic wording does not bind the question to the active workspace. The workspace becomes the target only when the user identifies it, supplies workspace evidence, or explicitly requests exact diagnosis or inspection. Explain common causes first; offer repository-specific inspection only conditionally when the user asks for the exact cause.

Lead with the resolved content. For a correction, accept it in the first sentence, state the corrected scope or non-goal, and do not revive the superseded direction as a requirement, solution, or verification target. For a causal explanation, answer the general cause and make any repository-specific diagnosis conditional instead of inspecting the current repository. For a direct how-to, give the shortest actionable method that satisfies the stated constraint. Add at most one short caveat when a real unresolved risk could change the answer.

Intake, security review, routing, and strict hook logging still run internally. Defer a missing manual pending-merge check until the next actionable turn. Do not inspect files, repositories, manifests, tools, credentials, or external state, and do not emit `[routing-surface]`, `[task-router]`, `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, or `[io-trace]` on the user surface. Do not load downstream skills or create a boundary contract.

This route is unavailable when the user requests a lookup, verification, file or state inspection, modification, external side effect, current or version-specific fact, support or regression judgment, or high-risk advice. Any required tool call or fresh evidence reclassifies the turn as `normal`.

## Examples

### Status During Implementation

The user authorized a parser fix and isolated regression test. The fix remains unwritten. The user asks, "What have you done?" Answer briefly from retained evidence in commentary, then implement and test the fix under the existing authorization. Do not send a final status report and wait for renewed permission.

### External Limit On One Branch

One provider's fixed quota prevents its live smoke test, while local reproduction and another supported execution path remain available. Preserve the provider-specific gap, implement and test the supported correction, and do not keep probing the unchanged quota. Passing the alternate branch does not prove the blocked branch passed.

### Explicit Pause And Finished Work

If the user says, "Stop editing and only explain the existing result," stop edits and give the bounded explanation. If the requested implementation and its acceptance tests are complete, a later question about retained evidence can use `direct-response`. Neither case creates new work from an old unresolved flag.

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
- A status answer or feedback record ends unfinished authorized work.
- A fixed branch-local obstacle is repeatedly checked while independent work remains.
