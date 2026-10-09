# task-router routing details

`SKILL.md` defines the routing procedure. This reference defines the full terminal-route contracts, boundary reconciliation, worked examples and failure modes.

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
response-mode: normal
output-skills: scheduling workflow if available
verification-skills: verification-before-completion
lifecycle: verification-before-completion
boundary-contract: required
boundary-reason: scheduling creates an external side effect
next-required: boundary-contract
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
boundary-contract: required
boundary-reason: requirements-table and body comparison is requested verification work
next-required: boundary-contract
```

The boundary owner establishes the comparison scope before extraction or verification. Then use an available extraction workflow when needed; if the supplied sources cannot be read, ask for the missing readable text. Read-only execution still requires the owning verification checks. The router does not invent filenames, scope permissions, or successful verification.

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
- task-router inserts an unowned prerequisite instead of consuming exact session-intent intake.
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

## Repair and check decisions

Actual current and prior human instructions supply the authorization envelope: objective, prohibitions, allowed actions and targets, side effects and resources. Retained authority facts preserve their human source; trusted system/project constraints retain their identified source. Intake compresses semantic facts and the router selects current work without granting authority. Distinguish human-owned criteria and resource choices from agent-derived interpretations and bounded local plans; an absent explicit resource cap is not an approved human limit. Renew a narrower implementation contract only inside actual authority, without weakening acceptance criteria or inferring permission from focus expansion. Request an exact human-owned decision before crossing its boundary. Actual grant renews only the affected plan and granted scope; denial parks it; no response leaves it waiting. Continue other eligible authorized work.

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.

The owning workflow calls the selected business action or corrective change and receives its actual result; declared action output may itself supply the verification observation. Domain verification returns actual observations, source and time to the workflow for criterion judgment. The applicable registered v2 pretool automatically prepares AP origin/receipt before the tool and returns current qualified command context. Reuse that valid prepared result; use an explicit returned preparation helper only when the applicable dependency remains genuinely unfulfilled. A recording gap affects capture/publication/application recordability; AP pretool continues and native tool permission remains with its host. Required or discriminating business observation follows its own authority and resource bounds; recording recovery rules govern recording attempts only. Preserve valid proof and its original source/time, including when recording is unavailable.

For a candidate closure, the owning workflow obtains or reuses supported proof. Applicable AP publication uses the exact current qualified selection, prepared AP receipt and actual returned publish command to leave a pending-adapter request. Native candidate/final dispatch invokes its actually registered handlers: the completion guard checks candidate fields and applicable skill evidence; the privileged AP Stop adapter revalidates publication, proof, ownership and dependencies. Only accepted current publication reaches the exact Core criterion writer within the shared authority transaction. Missing, stale or invalid publication returns a recording diagnostic without a criterion write. Actual application status and remaining criteria determine unit recording/completion claims. The handlers retain their actual native invocation semantics. Core owns delivery policy and may assemble the permitted payload; the workflow/native host performs visible delivery. Candidate claims use the supported current scope, while actual recording/application and visibility results govern further repair and closure claims. Preserve already-visible unchanged business content for applicable control-only repair. Return an admissible affected step to the workflow, or honest partial/waiting status when no eligible continuation remains.
