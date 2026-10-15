# Ghost-ALICE Autopilot Proof Publication

Core Python code selects admitted execution criteria from the current route, owner, work-unit ID and contract revision. Ghost-ALICE Autopilot adapter code records and applies supported proof for that selection. An existing run, cumulative history, missing route, planning reply or explanation does not create a publication obligation. Consume Core's `publication_route` (`none | prepare | publish | blocked`) instead of inferring it.

The compatible managed no-argument pretool/Stop chain selects `autopilot-completion-receipt.v2`; direct callers default to strict v1. Unknown capability remains incompatible or unavailable. Do not create criteria, approval, a foreign run or a cumulative-criteria fallback.

## Authority and route prerequisites

An explicit user command allowlist or tool-count limit includes agent-issued route, ledger, preparation and publication calls. Use valid existing context within that limit. A permitted action remains execution even when recording is unavailable. Preserve its actual result; do not fabricate a ready route or exceed the limit for governance. Native hooks and strict audit retain their registered behavior.

When a required capture or authorized next task depends on a missing or stale Core route, the current-session assistant loads `task-router` from the current host's skill catalog and follows step 6 in its `Select the next action` section, submitting `response-route.v1` through `_shared/response_route.py` or `record_response_route` with the exact current receipt binding. The [source-tree navigation link](../../../task-router/SKILL.md#select-the-next-action) does not identify an installed filesystem path: installed family skills may be flattened. Route absence does not invalidate business proof, require another business check, or grant additional tool authority.

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

Core records original locations, byte ranges and digests. The current-session assistant compares claims with the relevant originals and separately checks the complete answer against the user's objective. For new completion or partial-completion claims, or an authenticated explanation review explicitly requested by the user, it records those judgments through the Core APIs below. Routine source-backed explanations perform the two judgments using available original passages and locators, without these preparation and recording calls. A changed or missing original cannot support a new judgment; the original recorded judgment remains a historical fact. An idempotent recording call reuses its original judgment without repeating business verification.

The CLI returns references and status, not copies of source bodies or the complete answer. Use `_shared/source_lookup.py` with a literal search, line selection or JSON pointer to retrieve only the passages needed for the judgment; the common Context and Retention contract defines that reader. Ordinary replies supported entirely by supplied conversation do not require an execution criterion, verification timestamp or separate review process. The host displays answers through its native output path. Stop observes completion evidence without creating another model review or intercepting display.

Establish the following values before constructing the call. Use the current trusted runtime binding and the same receipt used for route registration; an ambient checkout, a cached variable or another session is not a substitute.

| Value | Authoritative origin and meaning |
| --- | --- |
| `CORE_API` | For an assistant tool call, resolve `../../_shared/completion_assessment.py` from the directory containing this installed reference file. The skill catalog supplies that absolute reference path. For explicit source-tree execution, use `<selected repository>/_shared/completion_assessment.py`. Native hooks use their registered runtime API. Each API loads its own deployment’s ledger and helpers; the calling shell needs no `GHOST_ALICE_CORE_ROOT`, `GHOST_ALICE_SKILLS_DIR` or `GHOST_ALICE_RUNTIME_SHARED_DIR`. A missing selected API is an installation error. |
| `LEDGER_ROOT`, `PLATFORM`, `SESSION_ID` | Current receipt's `ledger_root`, `platform` and `session_id`; after pending Claude reconciliation, use the authoritative state described in the ledger schema. Preserve the native `CODEX_THREAD_ID` binding; a mismatch is an error. |
| Current route | The ready owned route registered through task-router for that receipt/input. Use purpose `completion` for closure and `explanation` for an explicitly requested authenticated explanation review. A completion review selects exactly one owned work unit; when the route selects several, pass its exact `--work-unit-id`. |
| `ANSWER` | Absolute path in permitted runtime scratch to the complete proposed answer, including its actual completion/partial block. The assistant writes the draft through its exposed file tool. It includes every paragraph it intends to submit; a proof extract does not cover omitted prose. |
| `ORIGINAL_SOURCE` | Absolute path to an original observation or source whose contents support the draft. Repeat `--source` for each distinct source. Use the existing native tool record or original artifact without rerunning it or making another full copy. Preserve its locator/time; do not turn a later observation into historical proof. |
| `ORIGINAL_TIME` | The original timezone-bearing verification timestamp for a completion claim. Omit it for an explanation prepared from new source paths; retained completion proof keeps its original time. A later clock reading cannot supply a missing original time. |
| `CLAIMS` | For an explanation prepared from new source paths, an absolute permitted scratch path containing a JSON string array of the exact draft sentences or phrases the assistant will verify. The assistant selects these claims from the complete draft; Core does not infer them from prose. Completion/partial candidates instead obtain their claims from the control block. |

If a required binding or original observation is unavailable, report that recording dependency as unresolved; do not infer a module root from the ledger directory or invent a receipt. An explicitly requested authenticated explanation review needs its registered explanation route but does not need an execution criterion or previous completion record.

The assistant runs the following form through its exposed shell tool after binding every variable above. Each environment assignment applies to this process only. Repeat the same three assignments for `record-response`; do not change the native thread identity.

```bash
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" prepare-response \
  --response-file "$ANSWER" --source "$ORIGINAL_SOURCE" --verified-at "$ORIGINAL_TIME"
```

For an explanation prepared from new source paths, write the selected exact draft statements as a JSON string array at `CLAIMS`, pass `--claims-file "$CLAIMS"`, and omit `--verified-at`. Supply every source-backed assertion requiring verification; neither an empty list nor a whole-answer label substitutes for those claims. For retained completion proof, replace all new `--source` arguments with one `--reuse-candidate` or `--reuse-evidence` reference from the accepted original record and retain its verification time. An explanation of previously reviewed completion uses `--reuse-candidate` without `--claims-file`; it retains the original proof claim bindings and inherits the original verification time when `--verified-at` is omitted. Never combine newly read sources with historical reuse.

Core saves an immutable candidate in the receipt ledger. Its CLI receipt supplies `candidate_reference`, source IDs with their locators and digests, and `response_binding.binding_id`. It does not return source bodies or another copy of the answer; the caller retains the draft supplied through `--response-file`. Each `bindings[]` entry supplies a derived `binding_id` and `claim_index`; completion entries also identify the criterion by `id`, `work_unit_id` and `contract_revision`. The caller maps these coordinates to the claims and criteria it supplied, without copying their prose into another return. For retained proof whose original claim wording is needed, use the same validated source/session environment to invoke `python3 "$CORE_API" read-claims --candidate "$ORIGINAL_CANDIDATE" --index 0`, replacing the candidate and zero-based index with the selected original coordinates. This explicit lookup authenticates the original owner and returns only selected original bindings. Core derives the IDs; the assistant copies them from the receipt rather than constructing hashes or guessing names. The candidate retains authenticated original-source references and the compatible Ghost-ALICE Autopilot capture. Preparation is storage, not a favorable judgment.

The current-session assistant then performs two distinct model stages in order:

1. Map each returned `bindings[].claim_index` to its authored or explicitly retrieved original claim, then compare it with the original sources and, for completion, its identified criterion. Use each returned source locator to read the passages needed for that claim. Preparation returns references, not source bodies; use the original bytes when text decoding is insufficient. Produce a separate judgment per binding with the inspected `source_ids`, supporting/contradicting/unavailable evidence and its implication. Finish this claim comparison before reviewing the answer as a whole; explicitly requested authenticated explanations require these recorded claim judgments too.
2. Use those completed comparisons, the complete draft supplied through `--response-file`, and the current user's objective and constraints to judge the whole answer. Determine whether it supplies the requested result, respects scope, preserves unresolved criteria and avoids broader unsupported conclusions. Retain this separate judgment; agreement with individual sources alone does not establish whole-answer adequacy.

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

Supply one claim judgment per returned binding, with no missing or duplicate bindings. Each judgment uses `supported | unsupported | unavailable`, a substantive rationale and the IDs of sources actually inspected; a supported factual claim requires inspected sources. For unsupported, identify the conflicting original or the answer passages that cannot both hold under the same subject, time, conditions and scope. An internal contradiction needs no invented external source ID. Use unavailable when the necessary evidence cannot be established. An explicitly requested authenticated explanation cannot use `claims: []` or place its claim judgments only inside `response.rationale`. The `response` object records the separately reached whole-answer judgment. An explanation reusing a reviewed candidate retains its actual claim bindings and evidence under the reuse contract.

After both stages, bind `REFERENCE` to the returned `candidate_reference` and invoke:

```bash
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" record-response \
  --candidate "$REFERENCE" --judgment-file "$REVIEW"
```

When files for `ANSWER` or `REVIEW` are prohibited but Python execution is allowed, import `completion_assessment` from the directory containing the selected `CORE_API` through that tool. Call `prepare_response_review(source, response_text=<complete draft>, source_paths=<absolute original paths>, verified_at=<original time or None>, reuse_candidate=None, reuse_evidence=None, work_unit_id=None, claim_statements=None)`, supplying the selected exact draft statements as `claim_statements` only for an explanation prepared from new source paths. Leave that argument unset whenever a completion/partial block or proof-reuse argument selects original proof bindings. For a retained completion explanation, use `reuse_candidate` as described above. Then call `record_response_review(source, candidate_reference, review)`. Construct `source` from the current host environment and bind the three `GHOST_ALICE_*` keys above to their established values; retain native `CODEX_THREAD_ID` and any supplied managed capture/adapter context. Do not fabricate omitted runtime keys. The other arguments have exactly the same origins and shapes as the CLI. This avoids draft/review scratch files, not source requirements or either model stage.

Core code validates schema, source integrity, owner/input/contract bindings and the submitted judgments, then atomically stores the claim assessment and whole-answer review in the ledger. It does not compare meaning or decide truth. Successful recording returns `status: reviewed` and assessment/response references without the answer body. The current-session assistant submits the unchanged reviewed draft through native host output; native Stop supplies observation rather than delivery control. An internal recording operation does not itself require a new status answer. Atomic storage does not combine the two semantic stages, and matching bytes do not prove semantic support.

Read both exit status and returned JSON. `status: unsupported` records the unfavorable judgment and does not authorize submitting that draft; correct the actual claim or answer before preparing a changed candidate. A nonzero result returns `status: unavailable`, `reason` and `detail`; correct the named missing/stale binding, source or schema only when an authorized dependency requires it. A prepared candidate remains reusable only while its draft, sources and binding match. Changed draft/source content requires a new preparation and review of affected claims plus the complete answer. Preserve immutable judgments, supported original proof and original observation times. An unchanged failure does not trigger another submission or business execution.

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
| `continuation.status: pending`, `request_emitted: true` and `task_id`/`plan_id` | The adapter emitted a request. `host_receipt` and `execution` remain `unknown`; the task is not promoted to `running`. |
| A subsequent native conversation event contains that task request, and later tool results record its execution | Those distinct events establish host delivery and execution respectively. A Stop output alone establishes neither. |

Absent payloads or fields leave the corresponding stage unobserved. Do not invoke publication/application/Stop to obtain a read-only status. For current criterion state, use the receipt-bound `session_intent_ledger.py --read-state --pointer /acceptance_criteria/<index>` procedure; a `met` criterion alone does not establish host delivery. Reuse supplied native events when available; a new delivery lookup requires a user-authorized inspection task.

Native Stop observes the native response. Core's observer returns `continue: true` and preserves raw `response_assessment.body_issue` in strict audit. It sends no review-retry, route-repair or visibility-recovery prompt and does not hold or replace desktop output. Core and Ghost-ALICE Autopilot select the same body through `native_transcript.completion_scope()`, prioritizing inline text and requiring native ownership for transcript fallback. Unaccepted proof remains ineligible for Ghost-ALICE Autopilot publication/application. A Stop observation does not establish that the user saw the answer.

## Correct an accepted record

To correct a Ghost-ALICE Autopilot mapping, prepare with `--correction-of PUBLICATION_REF`, then use the returned publish command with the corrected proof and:

```text
--correction-json '{"supersedes":"PUBLICATION_REF","affected_criteria":[{"work_unit_id":"UNIT","contract_revision":1,"criterion_id":"CRITERION"}],"reason":"Mapping correction"}'
```

Core assessment must use `--reuse-evidence` with the exact predecessor evidence reference. Fresh sources with an old timestamp cannot authorize a correction. Ghost-ALICE Autopilot appends the correction and preserves the superseded record, original source and time.

Ghost-ALICE Autopilot compares the previously applied proof digest and current owner before replacing only the named qualified criteria. Unaffected criteria retain their proof. Stale comparisons, incompatible bindings and unsupported replacement judgments cannot overwrite application. Metadata correction creates no business verification and refreshes no clock.

## Continue approved tasks

The current-conversation assistant follows the installed `autopilot-mode` skill's `references/continuation.md`, section `Register the approved plan`, to judge and propose necessary authorized next work. Resolve that skill and its reference from the trusted Ghost-ALICE Autopilot installation binding; the task checkout and Core module root are not substitutes.

The assistant supplies its current receipt, selected qualified criteria, actual human authorization, allowed surfaces and executable task dependencies through that owning procedure. Conditional plan admission may precede completion application; it does not prove completion or dispatch. After authenticated criterion application, Ghost-ALICE Autopilot adapter code can select a dependency-ready admitted task and return a pending continuation request. Host transport of that request and any later authorized work require their own observations; returning the request establishes neither receipt nor execution.

Retain the returned plan/task IDs and distinguish admission, pending request emission, host receipt and actual task execution. Outstanding requests preserve their fingerprints without automatic retransmission; legacy dispatch entries preserve unknown receipt/execution. The adapter's result supplies no additional authority or missing model judgment.

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
