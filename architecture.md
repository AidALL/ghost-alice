# Ghost-ALICE Architecture

Language: English | [한국어](architecture_ko.md)

Ghost-ALICE helps the assistant turn a user request into authorized work, compare the result with original evidence, correct relevant mistakes and deliver an answer that accurately describes what was achieved. The same process can support development, research, documents and operations. The assistant chooses the scope and depth of verification from the unresolved question; more procedures or repeated checks are not goals.

## Who acts

The user supplies the objective, constraints and permission. The responding model interprets that request and makes the semantic judgments. Core and Ghost-ALICE Autopilot are instructions and executable code, not additional reasoning models. The native host is the Codex or Claude Code process that runs tools and registered hooks and delivers their output to a conversation.

| Actor | Responsibility |
| --- | --- |
| Model responding in the current conversation | Select authorized work; ask the host to run tools; compare claims with original sources; assess the complete answer against the user's objective; propose any needed, already authorized next tasks. |
| Core Python code | Record the current input and selected work; save the exact answer and source contents for review; validate and store the model's judgments; authenticate those records for native completion handlers. |
| Ghost-ALICE Autopilot Python code | Validate and store the model's approved task plan; preserve accepted proof; request guarded criterion updates; choose a task whose recorded prerequisites are satisfied. |
| Native host | Validate tool requests and enforce permission; execute the requested command or registered hook; return tool observations and hook feedback to the relevant conversation. |

A delegated model handles an explicitly assigned subtask and returns its result to its parent. A Stop-hook task request is different: the adapter returns it to the host for the conversation identified by that hook's native session. It does not spawn a delegated model or prove that the next task ran.

## Work, review and return paths

~~~mermaid
flowchart TD
  U["The user supplies the objective, constraints and permission"] -->|Objective and authority| S["1 · The responding model selects authorized work<br/>and asks the host to run the required tools"]
  S -->|Requested tool calls| T["The native host enforces tool permission, executes calls<br/>and returns their actual observations to the model"]
  T -->|Actual observations| S
  S -->|Original observations for the answer| D["2 · The model identifies the draft and source inputs<br/>New source-backed explanation: explicit claims<br/>Completion-candidate reuse: original bindings"]
  D -->|Draft and new claims/sources or original candidate| HP["The host execution tool runs<br/>completion_assessment.py prepare-response"]
  HP -->|CLI reads arguments and files| E["Core saves or retains exact review inputs<br/>and returns their contents and binding IDs"]
  E -->|Host returns saved contents and candidate_reference| V["3a · The model compares each claim with original source contents<br/>and gives a verdict, sources and reason"]
  V -->|Individual judgments| V2["3b · The model uses the individual judgments<br/>to compare the whole answer with the user objective,<br/>constraints and remaining work"]
  V2 -->|Claim judgments and whole-answer judgment| J["The model writes both judgments as review JSON<br/>and requests recording"]
  J -->|candidate_reference and review JSON file| HR["The host execution tool runs<br/>completion_assessment.py record-response"]
  HR -->|CLI calls record_response_review| C["Core validates bindings and source integrity,<br/>then stores both judgments in one transaction"]
  C -->|Supported: host returns review and response_text| R["4 · The model submits the exact reviewed answer<br/>returned by Core through the host tool result"]
  C -->|Unsupported review or named prerequisite error| N["The model corrects affected content or drafts an honest unresolved result<br/>It preserves unchanged business work<br/>and reopens only affected review inputs"]
  N -->|Only affected review inputs| D
  S -->|Current authority and approved task scope| P["5 · The model identifies needed approved tasks<br/>for separate plan admission before Stop<br/>Current answer review may follow admission"]
  P -->|Host Python execution request for the approved plan| HT["The host Python execution calls register_continuation_plan()<br/>with tasks, criterion references and actual approval references"]
  HT -->|Plan inputs passed to register_continuation_plan| Q["Ghost-ALICE Autopilot code validates tasks, dependencies,<br/>boundaries and approval references<br/>It returns an admitted plan reference or error"]
  Q -->|Host returns plan admission or a named error| P
  R -->|Submitted answer and native session| K["The native host invokes each registered Stop hook independently"]
  K -->|Independent completion-guard invocation| G["The Core completion guard calls assess_native_completion()<br/>and returns allow or a named rejection"]
  K -->|Independent Ghost-ALICE Autopilot Stop invocation| B["The Ghost-ALICE Autopilot Stop adapter calls the same assess_native_completion() API<br/>It publishes new proof only when Core selects it,<br/>applies or reuses eligible authenticated publications,<br/>and selects ready tasks from the admitted plan"]
  Q -->|Admitted plan retained for Stop| B
  G -->|Allow or named rejection| H["The host receives each hook result<br/>It may deliver feedback to the conversation identified by the hook"]
  B -->|Recording status and optional task request| H
  H -->|If the host delivers a named diagnostic to that conversation| X["If a correction is needed for the requested answer or an approved dependent task,<br/>that conversation’s next response model fixes the identified record error<br/>or revises or withdraws a claim unsupported by original evidence<br/>Receiving a diagnostic does not direct a business rerun"]
  H -->|If the host delivers an approved task request to that conversation| X2["That conversation’s next response model chooses permitted tools<br/>within the stored boundary and criteria<br/>Dispatch grants no new permission and proves<br/>neither execution nor whole-work completion"]
~~~

The diagram is a runtime overview. Its numbered handoffs are expanded below with exact values and APIs. The responding model makes the claim judgment in step 3a and the whole-answer judgment in step 3b. Saving both judgments atomically or validating their records performs neither judgment.

A supported answer can honestly report unfinished work; only supported required criteria can become met. Explanations create no new completion proof, while eligible prior proof and approved plans may still be used. A record creates no user permission.

### 1. Select work and execute it

The native input hook records the human input's digest and session event without saving the raw prompt. Codex intake commits directly. Claude intake first saves an observation; a trusted later PreToolUse or Stop wrapper compares the appended native actor records before committing human input. A task notification preserves the last human route, and an unresolved observation remains pending.

The model follows session-intent-analyzer to record a compressed intent change when needed, then task-router to select current work and its criteria. Through a host execution tool, it submits the selection and the exact receipt's root, platform, session and input event to `response_route.record_response_route()`. Core validates ownership, admission and the current input before returning the stored route. A route records the selection; the user's instruction supplies authority.

The model then requests the tools needed for that work. The host runs permitted calls and returns their actual observations. Applicable pretool hooks retain provenance and posttool hooks retain strict audit. Neither a saved plan nor a tool request is evidence that the business operation succeeded.

### 2. Send the answer and original sources to Core

The model prepares the complete answer draft and absolute paths to its original observations. For a new source-backed explanation, it explicitly lists the statements or passages under review as a JSON string array; each must occur in the draft. For completion or partial completion, the proof block supplies the claim bindings. An explanation that reuses an existing completion candidate retains its original bindings and sources under the owning reuse procedure. The model passes these inputs through a host execution request for Core `prepare-response`. Code does not infer claims from natural language.

| Handoff | Values actually passed | Receiving action and returned result |
| --- | --- | --- |
| Model → host execution tool → `completion_assessment.py prepare-response` | Draft path in `--response-file`. A new source-backed explanation supplies original paths through `--source` and its statement array through `--claims-file`. Completion proof supplies its original verification time; completion-candidate reuse retains original bindings without `--claims-file`, following the owning reuse procedure. The trusted runtime supplies the Core module root; the current receipt supplies the ledger root, platform and session. | `main()` reads the draft file and calls `prepare_response_review(source, response_text=..., source_paths=..., claim_statements=...)`. Core reads and saves source contents with the exact answer. Its JSON output returns the saved contents, `bindings[*].binding_id`, and `candidate_reference`. The host returns that JSON as the tool observation. |
| Model → host execution tool → `completion_assessment.py record-response` | The returned reference in `--candidate`; a file containing the model's `{claims, response}` judgments in `--judgment-file`. | `main()` parses the judgment file and calls `record_response_review()`. Core checks current bindings and source integrity, then records both judgments in one transaction. It returns status and record references; it includes the exact `response_text` only when the required judgments support that answer. |

Here, a *review-input record* means the saved answer, source copies and their current work context. The API calls it a candidate. A binding ID identifies the exact claim or answer being judged, and a source ID identifies a saved source. IDs connect the records; the model's comparison and rationale explain why the evidence supports, contradicts or leaves a claim unresolved. Saved records are not overwritten. A changed answer or source needs a new review-input record.

The [owning review procedure](coding-convention/verification-before-completion/references/autopilot-publication.md#prepare-and-record-the-response-review) supplies executable binding and command details, including original-proof and completion-candidate reuse. The interface above explains the transport without duplicating that procedure.

### 3. Make two distinct judgments

After receiving Core's saved review inputs through the tool result, the model performs these comparisons in order:

| Judgment | What the model reads | What the model decides |
| --- | --- | --- |
| Claim against original source | Each individual claim and the original source contents, including contrary observations. | Whether that claim is supported, unsupported or unavailable, with the relevant source references and a reason. |
| Whole answer against objective | The complete saved answer, the individual judgments, and the user's objective and constraints. | Whether the answer accurately addresses the requested scope, avoids omissions and conflicts, and honestly reports remaining work. |

The model then creates the judgment object and requests the recording call in handoff 2. Atomic storage keeps the two judgments together; it is not a third semantic judgment. A valid unsupported judgment can be recorded, but Core does not return answer text for final submission for it. The model revises the affected answer or reports the unresolved result through a new review.

An accurate answer can describe incomplete work; its supported response judgment does not fulfill the incomplete criterion. New source-backed explanations retain a separate `claims` judgment list for the model-selected statements before the whole-answer `response` judgment; completion-candidate reuse retains the original proof bindings. An explanation creates no completion criterion, completion block, new verification timestamp or Ghost-ALICE Autopilot publication. Ordinary replies supported entirely by the supplied conversation use the tool-free terminal procedure.

### 4. Submit the answer and receive independent Stop results

For the initial answer, the model submits the exact reviewed answer returned by Core. For a correction, the model selects and reviews the response to submit by following [Apply and deliver](coding-convention/verification-before-completion/references/autopilot-publication.md#apply-and-deliver). The host then invokes its independently registered Stop hooks. The Core completion guard and the Ghost-ALICE Autopilot adapter each obtain an input-and-answer-bound result from `native_completion.assess_native_completion()`. Neither must run before the other.

Core's assessment checks the current route, complete answer, original sources and recorded judgments. It returns authenticated supported coverage or a named missing prerequisite. The completion guard turns that result into the host's allow or rejection output. It does not compose missing prose or decide whether the evidence means what the model claimed.

The Ghost-ALICE Autopilot Stop path runs `autopilot_mode.main()` → `adapter_payload_from_env()` → `apply_completion_v2()` → `advance_continuation()`, then `_format_payload_for_hook()` serializes the result for the host. Completion recording and selection of later work are separate operations. The adapter publishes new completion proof only when Core selects publication. An explanation with `response-no-publication` creates no new proof; it may apply or reuse an existing authenticated publication with its original sources and time. Continuation can still select ready work from an admitted plan when current authority, dependencies and criterion states permit it. Supported criteria can be applied while other criteria remain unresolved; an entire work unit is complete only when all its required criteria are met.

### 5. Register and deliver authorized next work

When further work is needed, the responding model defines a plan within actual user approval. Plan admission may precede the current answer review; it must occur before Stop can select from that plan. Available reviewed results inform the plan without becoming an API admission prerequisite. It requests a host Python execution that calls `autopilot_continuation.register_continuation_plan()` with the current session/input, tasks, dependencies, allowed surfaces, exact owned criterion references and the location of the user's approval. This API has no dedicated CLI. The adapter validates those inputs and returns an admitted plan reference or an error. Saving a plan does not create permission or execute a task.

At Stop, `advance_continuation()` reads that stored plan and current criterion states. When a task is eligible, it records the dispatch and returns a task prompt with its boundary and criterion references. The formatter returns a Stop task request to the host. Only if the host delivers that feedback to the conversation identified by the native hook session does the model generating that conversation's next response receive it and choose its tool calls. A receipt identifies the conversation, not a persistent model instance.

## Mistakes, unresolved work and retained evidence

The model first asks whether a mismatch affects the objective or a required dependency. A needed authorized correction can change the relevant claim, action, subtask, dependency or assumption. An irrelevant detail can remain unresolved or be omitted from discretionary work; an explicit requirement cannot become successful by omission. The [focus diagram](https://github.com/AidALL/ghost-alice/wiki/operating-model-fallback-and-escalation#focus-and-verification-burden) explains how the investigation can widen or narrow. The [comparison diagram](https://github.com/AidALL/ghost-alice/wiki/design-philosophy-floor-not-ceiling#comparison-and-feedback) and [evidence procedure](https://github.com/AidALL/ghost-alice/wiki/operating-model-closed-loop-reasoning) explain the reasoning.

A missing current route is corrected through the route API; a malformed review field is corrected through the review workflow. Contrary evidence requires revising or withdrawing the unsupported claim. A recording-only failure preserves the business result and parks only dependent application. It does not request another business run or another copy of an already visible unchanged answer. Repeated unchanged recording failure remains parked until a relevant condition changes.

A new input requires its own route binding but does not refresh an original verification time or invalidate unchanged proof. An actual relevant artifact, dependency or criterion change reopens only affected evidence. Review and publication retain original sources, times and prior records. Core's capture binding establishes that provenance was captured before the review snapshot; it does not attest when an earlier business operation ran.

Core's delivery helper selects the shape of a corrected response from supplied host context. The model uses that context to preserve an unchanged visible answer, assemble one complete answer for final-only delivery, or submit corrected prose when claims change. It does not call tools merely to discover whether the host displayed the previous answer.

## What each record establishes

| Observation | What it establishes |
| --- | --- |
| A source edit | The named file contents changed. |
| A passing check | That check returned the retained passing observation for its target and time. |
| A managed installation | Selected source bytes were deployed to the recorded targets. Native loading requires a separate observation. |
| Accepted proof publication | Ghost-ALICE Autopilot accepted the proof for recording. Criterion application is a later guarded operation. |
| Applied criterion | The named qualified criterion became met. Other criteria and later work remain separate. |
| Returned Stop task request | The adapter selected and recorded eligible work. Host delivery and business execution require their own observations. |

SQLite owns session intent, admitted work, criteria and response routes. Ghost-ALICE Autopilot keeps its own provenance, publication, application and dispatch records. Compatibility JSON exports and discovery pointers cannot replace those owners.

The [session gate matrix](docs/policies/session-gate-matrix.md) maps conditions to their owning procedures. The [installer architecture](docs/reference/installer-architecture.md) explains deployment and hook registration. A shorter displayed message changes neither hook execution nor strict audit.
