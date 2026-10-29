# Response Review

Core Python code selects admitted execution criteria from the current route, owner, work-unit ID and contract revision. Core records the current-session assistant's review of a response against those criteria and its original sources; the Stop observer validates the delivered text against that record.

## Authority and route prerequisites

When a review or completion claim depends on a missing or stale Core route, the current-session assistant loads `task-router` from the current host's skill catalog and follows the route-registration step in its `Select the next action` section, submitting `response-route.v1` through `_shared/response_route.py` or `record_response_route` with the exact current receipt binding. The [source-tree navigation link](../../../task-router/SKILL.md#select-the-next-action) does not identify an installed filesystem path: installed family skills may be flattened.

Keep Core APIs, runtime modules, ledger, project checkout and installed skills as separate exact locations. A transported path or available protocol does not establish admission, origin, approval or native permission.

## Prepare and record the response review

Core records original locations, byte ranges and digests. For new completion or partial-completion claims, or an authenticated explanation review explicitly requested by the user, the current-session assistant records its per-claim and whole-answer judgments through the Core APIs below. Routine source-backed explanations perform the two judgments using available original passages and locators, without these preparation and recording calls. A changed or missing original cannot support a new judgment; the original recorded judgment remains a historical fact. An idempotent recording call reuses its original judgment without repeating business verification.

The CLI returns references and status, not copies of source bodies or the complete answer. Use `_shared/source_lookup.py` with a literal search, line selection or JSON pointer to retrieve only the passages needed for the judgment; task-router's `references/context-selection.md` defines that reader. Ordinary replies supported entirely by supplied conversation do not require an execution criterion, verification timestamp or separate review process.

Establish the following values before constructing the call. Use the current trusted runtime binding and the same receipt used for route registration; an ambient checkout, a cached variable or another session is not a substitute.

| Value | Authoritative origin and meaning |
| --- | --- |
| `CORE_API` | For an assistant tool call, resolve `../../_shared/completion_assessment.py` from the directory containing this installed reference file. The skill catalog supplies that absolute reference path. For explicit source-tree execution, use `<selected repository>/_shared/completion_assessment.py`. Native hooks use their registered runtime API. Each API loads its own deployment’s ledger and helpers; the calling shell needs no `GHOST_ALICE_CORE_ROOT`, `GHOST_ALICE_SKILLS_DIR` or `GHOST_ALICE_RUNTIME_SHARED_DIR`. A missing selected API is an installation error. |
| `LEDGER_ROOT`, `PLATFORM`, `SESSION_ID` | Current receipt's `ledger_root`, `platform` and `session_id`; after pending Claude reconciliation, use the authoritative state described in the ledger schema. Preserve the native `CODEX_THREAD_ID` binding; a mismatch is an error. |
| Current route | The ready owned route registered through task-router for that receipt/input. A completion review selects exactly one owned work unit; when the route selects several, pass its exact `--work-unit-id`. An explanation review binds an answer that carries no control block. |
| `ANSWER` | Absolute path in permitted runtime scratch to the complete proposed answer, including its actual completion/partial block. The assistant writes the draft through its exposed file tool. It includes every paragraph it intends to submit; a proof extract does not cover omitted prose. |
| `ORIGINAL_SOURCE` | Absolute path to an original observation or source whose contents support the draft. Repeat `--source` for each distinct source. Use the existing native tool record or original artifact without rerunning it or making another full copy. Preserve its locator/time; do not turn a later observation into historical proof. |
| `ORIGINAL_TIME` | The original timezone-bearing verification timestamp for a completion claim. Omit it for an explanation prepared from new source paths; retained completion proof keeps its original time. A later clock reading cannot supply a missing original time. |
| `CLAIMS` | For an explanation prepared from new source paths, an absolute permitted scratch path containing a JSON string array of the exact draft sentences or phrases the assistant will verify. The assistant selects these claims from the complete draft; Core does not infer them from prose. Completion/partial candidates instead obtain their claims from the control block. |

If a required binding or original observation is unavailable, report that recording dependency as unresolved; do not infer a module root from the ledger directory or invent a receipt. An explicitly requested authenticated explanation review needs the current route but not an execution criterion or a previous completion record.

The assistant runs the following form through its exposed shell tool after binding every variable above. Each environment assignment applies to this process only. Repeat the same three assignments for `record-response`; do not change the native thread identity.

```bash
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" prepare-response \
  --response-file "$ANSWER" --source "$ORIGINAL_SOURCE" --verified-at "$ORIGINAL_TIME"
```

For an explanation prepared from new source paths, write the selected exact draft statements as a JSON string array at `CLAIMS`, pass `--claims-file "$CLAIMS"`, and omit `--verified-at`. Supply every source-backed assertion requiring verification; neither an empty list nor a whole-answer label substitutes for those claims. For retained completion proof, replace all new `--source` arguments with one `--reuse-candidate` reference to the original reviewed candidate and retain its verification time. An explanation of previously reviewed completion uses `--reuse-candidate` without `--claims-file`; it retains the original proof claim bindings and inherits the original verification time when `--verified-at` is omitted. Never combine newly read sources with historical reuse.

Core saves an immutable candidate in the receipt ledger. Its CLI receipt supplies `candidate_reference`, source IDs with their locators and digests, and `response_binding.binding_id`. It does not return source bodies or another copy of the answer; the caller retains the draft supplied through `--response-file`. Each `bindings[]` entry supplies a derived `binding_id` and `claim_index`; completion entries also identify the criterion by `id`, `work_unit_id` and `contract_revision`. The caller maps these coordinates to the claims and criteria it supplied, without copying their prose into another return. For retained proof whose original claim wording is needed, use the same validated source/session environment to invoke `python3 "$CORE_API" read-claims --candidate "$ORIGINAL_CANDIDATE" --index 0`, replacing the candidate and zero-based index with the selected original coordinates. This explicit lookup authenticates the original owner and returns only selected original bindings. Core derives the IDs; the assistant copies them from the receipt rather than constructing hashes or guessing names. The candidate retains authenticated original-source references. Preparation is storage, not a favorable judgment.

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

When files for `ANSWER` or `REVIEW` are prohibited but Python execution is allowed, import `completion_assessment` from the directory containing the selected `CORE_API` through that tool. Call `prepare_response_review(source, response_text=<complete draft>, source_paths=<absolute original paths>, verified_at=<original time or None>, reuse_candidate=None, work_unit_id=None, claim_statements=None)`, supplying the selected exact draft statements as `claim_statements` only for an explanation prepared from new source paths. Leave that argument unset whenever a completion/partial block or proof-reuse argument selects original proof bindings. For a retained completion explanation, use `reuse_candidate` as described above. Then call `record_response_review(source, candidate_reference, review)`. Construct `source` from the current host environment and bind the three `GHOST_ALICE_*` keys above to their established values; retain the native `CODEX_THREAD_ID`. Do not fabricate omitted runtime keys. The other arguments have exactly the same origins and shapes as the CLI. This avoids draft/review scratch files, not source requirements or either model stage.

Core code validates schema, source integrity, owner/input/contract bindings and the submitted judgments, then atomically stores the claim assessment and whole-answer review in the ledger. It does not compare meaning or decide truth. Successful recording returns `status: reviewed` and assessment/response references without the answer body. The current-session assistant submits the unchanged reviewed draft through native host output. An internal recording operation does not itself require a new status answer. Atomic storage does not combine the two semantic stages, and matching bytes do not prove semantic support.

Read both exit status and returned JSON. `status: unsupported` records the unfavorable judgment and does not authorize submitting that draft; correct the actual claim or answer before preparing a changed candidate. A nonzero result returns `status: unavailable`, `reason` and `detail`; correct the named missing/stale binding, source or schema only when an authorized dependency requires it. A prepared candidate remains reusable only while its draft, sources and binding match. Changed draft/source content requires a new preparation and review of affected claims plus the complete answer. Preserve immutable judgments, supported original proof and original observation times. An unchanged failure does not trigger another submission or business execution.

The explanation review records the model's judgments; it does not satisfy a work criterion.

## Proof bytes and time

The main verification skill owns completion and partial-control schemas. Keep exactly one nonempty top-level `- evidence:` section in new proof. Core reads the exact source-reference text from it; nested claim evidence cannot substitute. Missing, empty, duplicate, ambiguous or placeholder evidence remains rejected.

Supply the original raw timestamp through `--verified-at`; Core retains that `verified_at` as audit data. Do not add or refine a timestamp in unchanged proof.

## Deliver

A recording-only error is retained without blocking Stop or requesting answer resubmission. Changed claims remain subject to Core verification.

Native Stop observes the native response. Core's observer returns `continue: true` and preserves raw `response_assessment.body_issue` in strict audit. It sends no review-retry, route-repair or visibility-recovery prompt and does not hold or replace desktop output. Core selects the body through `native_transcript.completion_scope()`, prioritizing inline text and requiring native ownership for transcript fallback. A Stop observation does not establish that the user saw the answer.

Do not invoke Stop to obtain a read-only status. For current criterion state, use the receipt-bound `session_intent_ledger.py --read-state --pointer /acceptance_criteria/<index>` procedure; a `met` criterion alone does not establish host delivery. Reuse supplied native events when available; a new delivery lookup requires a user-authorized inspection task.

## Recording-only failures

Preserve original business proof after a binding or recording failure. The current-session assistant first identifies whether the missing record prevents a required claim or an authorized dependent task. If it does not, retain the recording gap without another command or user-facing repair response. An unmet required recording condition stays unresolved.

When a required dependency needs correction, repair only that record: task-router for the current route, Core assessment for a source-to-claim judgment. Retain the original receipt/reference. An identical unchanged failure suspends that attempt until a relevant state change; independent authorized work continues. Do not fabricate origin, permission, admission, approval, run state or source authority.

If additional task files are prohibited, use permitted stdin or runtime scratch rather than creating them.

Systematic-debugging owns [incident accounting](../../systematic-debugging/SKILL.md#incident-accounting-and-continuation); verification-before-completion owns [useful checks](../SKILL.md#evidence-selection-and-stop-gate); boundary-contract owns authority. Recording-specific suspension does not reset those limits.
