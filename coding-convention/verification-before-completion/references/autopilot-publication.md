# Ghost-ALICE Autopilot Proof Publication

Core Python code selects admitted execution criteria from the current route, owner, work-unit ID and contract revision. Ghost-ALICE Autopilot adapter code records and applies supported proof for that selection. An existing run, cumulative history, missing route, planning reply or explanation does not create a publication obligation. Consume Core's `publication_route` (`none | prepare | publish | blocked`) instead of inferring it.

The compatible managed no-argument pretool/Stop chain selects `autopilot-completion-receipt.v2`; direct callers default to strict v1. Unknown capability remains incompatible or unavailable. Do not create criteria, approval, a foreign run or a cumulative-criteria fallback.

## Authority and route prerequisites

An explicit user command allowlist or tool-count limit includes agent-issued route, ledger, preparation and publication calls. Use valid existing context within that limit. A permitted action remains execution even when recording is unavailable. Preserve its actual result; do not fabricate a ready route or exceed the limit for governance. Native hooks and strict audit retain their registered behavior.

When a required capture or authorized next task depends on a missing or stale Core route, the current-session assistant loads `task-router` from the current host's skill catalog and follows its `Register the Core projection` section, submitting `response-route.v1` through `_shared/response_route.py` or `record_response_route` with the exact current receipt binding. The [source-tree navigation link](../../../task-router/SKILL.md#register-the-core-projection) does not identify an installed filesystem path: installed family skills may be flattened. Route absence does not invalidate business proof, require another business check, or grant additional tool authority.

Keep Core APIs, runtime modules, ledger, project checkout and installed skills as separate exact locations. A transported path or available protocol does not establish admission, origin, approval or native permission.

## Prepare and retain a result

For the exact current qualified selection, the registered v2 pretool prepares the Ghost-ALICE Autopilot origin/receipt internally. The assistant reuses that preparation. Do not run its `prepare_command` again merely because verification is about to occur.

If the preparation dependency remains unfulfilled and Core requires it, use the explicit returned helper within the validated source/session environment:

```text
autopilot_completion.py prepare --intent-root ROOT --platform PLATFORM --session-id SESSION --input-event-id INPUT --schema-version autopilot-completion-receipt.v2
```

Replace every coordinate from the current receipt. Ghost-ALICE Autopilot retains the target, qualified criteria, receipt and original capture. The receipt establishes prospective recording provenance; it does not verify the business result or require `approved-run.json` or `tasks.jsonl`.

Ghost-ALICE Autopilot's pretool returns `continue: true` even when capture fails. Such a failure can prevent publication/application recordability, but Ghost-ALICE Autopilot does not grant or deny business authority or host tool permission. Preserve the business result and report the recording gap separately.

For a genuinely new verification, the current-session assistant retains the actual observation and its recorded verification time. For retained proof, it keeps the original source and time. Core records the assessment’s causal link to the existing Ghost-ALICE Autopilot capture automatically; the assistant does not need a separate clock command or a manually refined timestamp to establish that link.

## Prepare and record the response review

This is the executable handoff for a new completion/partial claim or a source-backed explanation on a normal route. The current-session assistant performs the meaning review. Its exposed shell or Python execution tool calls Core code to save and authenticate that review; `prepare_response_review` and `record_response_review` are Python APIs, not additional model tools. Routine conversation-only explanation remains tool-free.

Establish the following values before constructing the call. Use the current trusted runtime binding and the same receipt used for route registration; an ambient checkout, a cached variable or another session is not a substitute.

| Value | Authoritative origin and meaning |
| --- | --- |
| `CORE_ROOT` | Preserve the registered `GHOST_ALICE_CORE_ROOT` source coordinate, or its supplied `GHOST_ALICE_SKILLS_DIR` fallback. This coordinate identifies the installation source; it does not select libraries for an installed hook. |
| `CORE_API` | With a registered `GHOST_ALICE_RUNTIME_SHARED_DIR`, use its absolute `completion_assessment.py` path. Without that runtime binding, an explicitly selected source execution uses `CORE_ROOT/_shared/completion_assessment.py`. The API loads the ledger and helpers from its own deployment. A missing selected API is an installation error, not permission to switch to another copy. |
| `LEDGER_ROOT`, `PLATFORM`, `SESSION_ID` | Current receipt's `ledger_root`, `platform` and `session_id`; after pending Claude reconciliation, use the authoritative state described in the ledger schema. Preserve the native `CODEX_THREAD_ID` binding; a mismatch is an error. |
| Current route | The ready owned route registered through task-router for that receipt/input. Use purpose `completion` for closure and `explanation` for source-backed explanation. A completion review selects exactly one owned work unit; when the route selects several, pass its exact `--work-unit-id`. |
| `ANSWER` | Absolute path in permitted runtime scratch to the complete proposed answer, including its actual completion/partial block. The assistant writes the draft through its exposed file tool. It includes every paragraph it intends to submit; a proof extract does not cover omitted prose. |
| `ORIGINAL_SOURCE` | Absolute path to an original observation or source whose contents support the draft. Repeat `--source` for each distinct source. Retain or copy original tool output without rerunning it, and preserve its locator/time; do not turn a later observation into historical proof. |
| `ORIGINAL_TIME` | The original timezone-bearing verification timestamp for a completion claim. Omit it for an explanation prepared from new source paths; retained completion proof keeps its original time. A later clock reading cannot supply a missing original time. |
| `CLAIMS` | For an explanation prepared from new source paths, an absolute permitted scratch path containing a JSON string array of the exact draft sentences or phrases the assistant will verify. The assistant selects these claims from the complete draft; Core does not infer them from prose. Completion/partial candidates instead obtain their claims from the control block. |

If a required binding or original observation is unavailable, report that recording dependency as unresolved; do not infer a module root from the ledger directory or invent a receipt. A source-backed explanation first needs its registered explanation route but does not need an execution criterion or previous completion record.

The assistant runs the following form through its exposed shell tool after binding every variable above. Each environment assignment applies to this process only. Repeat the same four assignments for `record-response`; do not change the native thread identity.

```bash
GHOST_ALICE_CORE_ROOT="$CORE_ROOT" \
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" prepare-response \
  --response-file "$ANSWER" --source "$ORIGINAL_SOURCE" --verified-at "$ORIGINAL_TIME"
```

For an explanation prepared from new source paths, write the selected exact draft statements as a JSON string array at `CLAIMS`, pass `--claims-file "$CLAIMS"`, and omit `--verified-at`. Supply every source-backed assertion requiring verification; neither an empty list nor a whole-answer label substitutes for those claims. For retained completion proof, replace all new `--source` arguments with one `--reuse-candidate` or `--reuse-evidence` reference from the accepted original record and retain its verification time. An explanation of previously reviewed completion uses `--reuse-candidate` without `--claims-file`; it retains the original proof claim bindings and inherits the original verification time when `--verified-at` is omitted. Never combine newly read sources with historical reuse.

Core's `prepare_response_review()` saves an immutable candidate in the receipt ledger's `core_completion_candidates` table. Its JSON tool result supplies `candidate_reference`, `sources[].source_id` with `locator`, `text` and `bytes_base64`, and `response_binding.binding_id` with the complete `text`. The result also supplies `bindings[]` with one derived `binding_id` per declared claim. Completion bindings carry the control block's `claim`, `criterion`, `evidence` and `declared_verdict`; new-source explanation bindings identify the assistant-supplied statement, while an explanation reusing completion proof retains its original claim bindings. Core derives these IDs; the assistant copies them from this return rather than constructing hashes or guessing names. The candidate retains the original source bytes and compatible Ghost-ALICE Autopilot capture. Preparation is storage, not a favorable judgment.

The current-session assistant then performs two distinct model stages in order:

1. Compare every returned `bindings[]` claim with the candidate's original sources and, for completion, its qualified criterion. Read the returned source contents, using original bytes when text decoding is insufficient. Produce a separate judgment per binding with the inspected `source_ids`, supporting/contradicting/unavailable evidence and its implication. Finish this claim comparison before reviewing the answer as a whole; explanations require these recorded claim judgments too.
2. Use those completed comparisons, the complete `response_binding.text`, and the current user's objective and constraints to judge the whole answer. Determine whether it supplies the requested result, respects scope, preserves unresolved criteria and avoids broader unsupported conclusions. Retain this separate judgment; agreement with individual sources alone does not establish whole-answer adequacy.

Bind `REVIEW` to an absolute permitted runtime scratch path, then use the exposed file tool to write a JSON object with exactly `claims` and `response` at that path. The following is a shape example; copy every ID from the prepared candidate and replace the sample rationale with the actual comparison:

```json
{
  "claims": [{
    "binding_id": "<one bindings[].binding_id from prepare>",
    "verdict": "supported",
    "source_ids": ["<inspected sources[].source_id>"],
    "rationale": "The identified original observation supports this exact claim and criterion because ..."
  }],
  "response": {
    "binding_id": "<response_binding.binding_id from prepare>",
    "verdict": "supported",
    "source_ids": ["<inspected sources[].source_id>"],
    "rationale": "Using the completed claim comparisons, the complete answer satisfies the stated objective and constraints because ..."
  }
}
```

Supply one claim judgment per returned binding, with no missing or duplicate bindings. Each judgment uses `supported | unsupported | unavailable`, a substantive rationale and the IDs of sources actually inspected; a supported/unsupported judgment requires a nonempty source list. A source-backed explanation cannot use `claims: []` or place its claim judgments only inside `response.rationale`. The `response` object records the separately reached whole-answer judgment. An explanation reusing a reviewed candidate retains its actual claim bindings and evidence under the reuse contract.

After both stages, bind `REFERENCE` to the returned `candidate_reference` and invoke:

```bash
GHOST_ALICE_CORE_ROOT="$CORE_ROOT" \
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" record-response \
  --candidate "$REFERENCE" --judgment-file "$REVIEW"
```

When files for `ANSWER` or `REVIEW` are prohibited but Python execution is allowed, import `completion_assessment` from the directory containing the selected `CORE_API` through that tool. Call `prepare_response_review(source, response_text=<complete draft>, source_paths=<absolute original paths>, verified_at=<original time or None>, reuse_candidate=None, reuse_evidence=None, work_unit_id=None, claim_statements=None)`, supplying the selected exact draft statements as `claim_statements` only for an explanation prepared from new source paths. Leave that argument unset whenever a completion/partial block or proof-reuse argument selects original proof bindings. For a retained completion explanation, use `reuse_candidate` as described above. Then call `record_response_review(source, candidate_reference, review)`. Construct `source` from the current host environment and bind the four `GHOST_ALICE_*` keys above to their established values; retain native `CODEX_THREAD_ID` and any supplied managed capture/adapter context. Do not fabricate omitted runtime keys. The other arguments have exactly the same origins and shapes as the CLI. This avoids draft/review scratch files, not source requirements or either model stage.

Core code validates schema, source integrity, owner/input/contract bindings and the submitted judgments, then atomically stores the claim assessment and whole-answer review in the ledger. It does not compare meaning or decide truth. Successful recording returns `status: reviewed` and the exact supported `response_text`, with assessment/response reference fields as applicable. For the initial answer, the assistant submits that returned text to the host; native Stop authenticates its reviewed content. When correcting a prior delivered answer, select the payload described in Apply and deliver before preparing its review; returned text is not a command to replay an already visible answer. Atomic storage does not combine the two semantic stages, and matching bytes do not prove semantic support.

Read both exit status and returned JSON. `status: unsupported` records the unfavorable judgment and returns no deliverable `response_text`; correct the actual claim or answer before preparing a changed candidate. A nonzero result returns `status: unavailable`, `reason` and `detail`; repair only the named missing/stale binding, source or schema when authorized. If review is missing, reuse the already prepared candidate only while its draft, sources and binding still match. Changed draft/source content requires a new preparation and review of affected claims plus the complete answer. Do not overwrite an immutable unfavorable review with a more favorable label, resubmit an unchanged failed record without a relevant change, or replay business work to repair recording. Preserve supported original proof and follow Core's delivery instruction when an answer is already visible.

The explanation review itself records the model's judgments; it creates no new Ghost-ALICE Autopilot publication and does not satisfy a work criterion. At Stop, the adapter may separately apply an existing authenticated publication to its still-unapplied criteria when that publication remains eligible. This reuse does not turn the explanation review into completion proof. Publication and application below consume authenticated records; they do not supply missing model judgments.


## Native publication and manual CLI

At native Stop, Ghost-ALICE Autopilot reads Core's authenticated whole-answer review, resolves the current publication receipt and original compatible capture, and calls its canonical publication service. It then applies the accepted criteria through the guarded Core writer. The assistant submits no separate publication command. Ghost-ALICE Autopilot runs no business checks and supplies no missing meaning judgment.

For an explicit manual or compatibility caller, use the returned `publish_command`, reviewed answer file and original `--verified-at` value. The following CLI forms remain available for retained proof and corrections.

For already accepted unchanged proof, use the current returned binding:

```text
autopilot_completion.py publish --receipt-token CURRENT_TOKEN --evidence-reference ORIGINAL_REFERENCE --schema-version autopilot-completion-receipt.v2
```

Repeat `--evidence-reference` for independent accepted records. Supply neither new proof bytes nor a new verification time. Changed criteria, dependencies or incompatible owner/target reopen only affected coverage.

A new user input changes publication authority, not compatible original capture. The managed path resolves the original receipt from the authenticated Core review. Manual callers with artifact dependency snapshots use:

```text
publish --receipt-token CURRENT_TOKEN --origin-receipt-token ORIGINAL_TOKEN --original-dependencies-json ORIGINAL_SNAPSHOTS --verified-at ORIGINAL_TIME --completion-file ORIGINAL_PROOF --schema-version autopilot-completion-receipt.v2
```

The original token supplies capture provenance; the current token supplies publication authority. Both receipts must select compatible exact owner, ledger, target, contract revision, relevant context and proof criteria. `ORIGINAL_SNAPSHOTS` is the retained list `[{"locator":"/absolute/artifact","digest":"sha256:..."}]`, or explicit `[]` when there were no artifact dependencies. Do not hash a changed artifact later and call that its original snapshot.

The helper compares current hashes with original values without replacing them. Missing original backing, changed contracts or unavailable prospective capture remain rejected. Do not combine this publication form with `--dependency` or accepted `--evidence-reference` reuse, and do not replay unchanged business checks to repair a record.

## Proof bytes and time

The main verification skill owns completion and partial-control schemas. Keep exactly one nonempty top-level `- evidence:` section in new proof. Ghost-ALICE Autopilot derives the exact source-reference text from it; nested claim evidence cannot substitute. Missing, empty, duplicate, ambiguous or placeholder evidence remains rejected.

Supply the original raw timestamp through `--verified-at`; Core and Ghost-ALICE Autopilot retain that `verified_at` as audit data. Do not add or refine a timestamp in unchanged proof merely for publication.

Core’s saved review-input record binds the review to the earliest authenticated Ghost-ALICE Autopilot capture for the same owner, target, contract and covered criteria. Ghost-ALICE Autopilot separately checks that the current publication receipt belongs to the current input. Ghost-ALICE Autopilot authenticates the resulting `assessment_capture_binding` to establish capture-before-review order, including when the recorded times overlap within one second. The assistant does not enter microseconds or choose a later time to make that comparison pass. The causal record establishes when this evidence review was prepared; it does not attest that the host executed the historical business action at the caller’s reported time.

When a legacy record lacks that causal link and its retained times cannot establish order, recording remains `time-order-ambiguous`. Preserve the original proof and timestamp and report the unresolved record. A new clock reading, backdated declaration or replay of unchanged business work cannot supply the missing historical link.

An explicit `--evidence-source` override must be nonempty and match the proof's unchanged evidence reference.

## Apply and deliver

A recording-only error is retained without blocking Stop or requesting answer resubmission; dependent application waits for a relevant state change. Changed claims remain subject to Core verification.

Publication acceptance means pending application. At native Stop, the completion guard and Ghost-ALICE Autopilot consume Core's exact input/body-bound assessment; hook ordering is not a substitute for that shared decision. Ghost-ALICE Autopilot authenticates publication/archive integrity and current bindings, then applies accepted admitted criteria through the Core writer's transaction.

For partial proof, a criterion applies only when every mapped claim passes and no unresolved entry names it. Mixed pass/fail, uncovered and unresolved criteria stay unmet. The work unit stays pending until all required criteria are satisfied. Legacy whole-item `continue_next` requires final all-pass proof.

Read the returned adapter payload or its retained original hook result for the exact session and work-unit/revision. The following fields establish different observations:

| Returned evidence | Supported conclusion |
| --- | --- |
| Publication result has `status: pending-adapter` and `publication_reference` | The service accepted the publication; criterion application is still pending. |
| `completion_recording.units[]` row has `status: applied` and a nonempty `application_reference` | The adapter committed the application identified by that reference. Use its `selected_criteria` for coverage. |
| The same row has `unit_status: complete` and no `remaining_criteria` or reported error | The returned observation supports the whole selected unit. `status: applied` alone can describe partial coverage. |
| `continuation.status: dispatched` and `task_id`/`plan_id` | The adapter recorded dispatch of that plan task. This is not an observation of host delivery or task execution. |
| A subsequent native conversation event contains that task request, and later tool results record its execution | Those distinct events establish host delivery and execution respectively. A Stop output alone establishes neither. |

Absent payloads or fields leave the corresponding stage unobserved. Do not invoke publication/application/Stop to obtain a read-only status. For current criterion state, use the receipt-bound `session_intent_ledger.py --read-state` procedure; a `met` criterion alone does not establish host delivery. Reuse supplied native events when available; a new delivery lookup requires a user-authorized inspection task. For a correction, Core's completion guard places delivery guidance in the Stop JSON `reason` when blocking, or `systemMessage` when returning a notice. The host conveys that string as hook feedback to the current conversation. Inside that string, `delivery-route:` is the selector value; `repair-target:` identifies `response_route` or `response_review` when applicable. These labels are feedback fields, not completion proof.

The assistant reads this supplied feedback before constructing another response:

| `delivery-route` | Response to submit |
| --- | --- |
| `preserve_answer_and_patch_metadata` | Corrected control fields only; the unchanged answer is already visible. |
| `assembled_complete_payload` | The retained answer and corrected controls together, once. |
| `corrected_complete_answer` | The complete answer with the changed claim and the remaining supported claims. |
| `resolve_host_delivery` | Use supplied host/conversation context: visible answer means correction only; final-only display means answer plus correction once; unknown visibility means correction only without claiming delivery. Do not run tools to discover screen visibility. |

The assistant prepares and reviews the exact replacement payload selected above before submitting it. A previous review of a longer answer does not authenticate newly changed control text. When `repair-target` is `response_route` or `response_review` and the business text is unchanged, follow the named registration/review procedure using the retained answer and evidence. That internal write does not itself require another user-facing answer, completion block or recording-status announcement. If the host requires a replacement response, apply its supplied delivery guidance to that payload. The visibility restriction concerns finding out what the user saw; it does not prohibit otherwise authorized tools for route or review records.

## Correct an accepted record

To correct a Ghost-ALICE Autopilot mapping, prepare with `--correction-of PUBLICATION_REF`, then use the returned publish command with the corrected proof and:

```text
--correction-json '{"supersedes":"PUBLICATION_REF","affected_criteria":[{"work_unit_id":"UNIT","contract_revision":1,"criterion_id":"CRITERION"}],"reason":"Mapping correction"}'
```

Core assessment must use `--reuse-evidence` with the exact predecessor evidence reference. Fresh sources with an old timestamp cannot authorize a correction. Ghost-ALICE Autopilot appends the correction and preserves the superseded record, original source and time.

Ghost-ALICE Autopilot compares the previously applied proof digest and current owner before replacing only the named qualified criteria. Unaffected criteria retain their proof. Stale comparisons, incompatible bindings and unsupported replacement judgments cannot overwrite application. Metadata correction creates no business verification and refreshes no clock.

## Continue approved tasks

The current-conversation assistant follows the installed `autopilot-mode` skill's `references/continuation.md`, section `Register the approved plan`, to judge and propose necessary authorized next work. Resolve that skill and its reference from the trusted Ghost-ALICE Autopilot installation binding; the task checkout and Core module root are not substitutes.

The assistant supplies its current receipt, selected qualified criteria, actual human authorization, allowed surfaces and executable task dependencies through that owning procedure. Conditional plan admission may precede completion application; it does not prove completion or dispatch. After authenticated criterion application, Ghost-ALICE Autopilot adapter code can select a dependency-ready admitted task and return a continuation callback. The native host transports that callback to the conversation; the model answering there performs the next authorized work through host tools.

Retain the returned plan/task IDs and distinguish admission, recorded dispatch, host delivery and actual task execution. The adapter's result supplies no additional authority or missing model judgment.

## Recording-only failures

Preserve original business proof after a binding, provider or publication failure. The current-session assistant first identifies whether the missing record prevents a required claim, criterion application or authorized dependent task. If it does not, retain the recording gap without another command or user-facing repair response. An unmet required recording condition stays unresolved.

When a required dependency needs correction, repair only that record: task-router for the current route, Core assessment for a source-to-claim judgment, or the returned Ghost-ALICE Autopilot operation for a receipt/publication defect. Retain the original receipt/reference. An identical unchanged failure suspends that attempt until a relevant state change; independent authorized work continues. Do not fabricate origin, permission, admission, approval, run state or source authority.

When the selected criterion or relevant artifact actually changes, observe only the affected behavior. Otherwise do not generate a later timestamp through business replay. If additional task files are prohibited, use permitted stdin or runtime scratch rather than creating them.

Systematic-debugging owns [incident accounting](../../systematic-debugging/SKILL.md#incident-accounting-and-continuation); verification-before-completion owns [useful checks](../SKILL.md#evidence-selection-and-stop-gate); boundary-contract owns authority. Recording-specific suspension does not reset those limits. Preserve strict hook execution and audit.

## Explicit v1 callers

A caller deliberately selecting v1 uses its generation-bound preparation:

```text
prepare --reapprove-current-input --intent-root ROOT --platform PLATFORM --session-id SESSION --input-event-id INPUT
```

Publish with the returned command, unchanged proof and original verification time after that capture. Any single explicit `verified-at` or `verified_at` declaration must match the raw timestamp exactly. The v2 causal assessment binding does not replace v1 generation/capture requirements. Do not apply v1 generation/capture requirements to accepted v2 reference reuse or loosen v1 provenance to imitate v2.
