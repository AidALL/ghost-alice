# Response Review

Core code selects acceptance criteria of execution work whose `selectable` flag the intent ledger stores as true, using the current response route, owner, work-unit ID and contract revision. Core code sets the flag by default from the source label unless the response model supplies an explicit value; the flag makes an acceptance criterion available for selection and does not prove the human user's approval. Core code records the response model's review of a response against those acceptance criteria and its original sources; Core code's Stop script compares the delivered text with that record.

## Authority and response route prerequisites

When a review or completion claim depends on a missing or stale response route in Core code, the response model loads `task-router` from the current host's skill catalog and follows the response route registration step in its `Select the next action` section, submitting `response-route.v2` through `_shared/response_route.py` or `record_response_route` with the exact current session metadata. The [source-tree navigation link](../../../task-router/SKILL.md#select-the-next-action) does not identify an installed filesystem path: installed family skills may be flattened.

Keep Core code APIs, runtime modules, intent ledger, project checkout and installed skills as separate exact locations. A transported path or available protocol does not establish admission, origin or approval.

## Prepare and record the response review

Core code records original locations, byte ranges and digests. For new completion or partial completion claims, or an authenticated explanation review that the human user explicitly requested, the response model records its per-claim and whole-response judgments through the Core code APIs below. For a routine source-backed explanation, the response model performs the two judgments using available original passages and locators, without these preparation and recording calls. A changed or missing original cannot support a new judgment; the original recorded judgment remains a historical fact. An idempotent recording call reuses its original judgment without repeating verification of the work.

The CLI returns references and status, not copies of source bodies or the complete answer. Use `_shared/source_lookup.py` with a literal search, line selection or JSON pointer to retrieve only the passages needed for the judgment; task-router's `references/context-selection.md` defines that reader. Ordinary replies supported entirely by supplied conversation do not require an acceptance criterion of execution work, verification timestamp or separate review process.

Establish the following values before constructing the call. Use the current trusted runtime binding and the same session metadata used for response route registration.

| Value | Authoritative origin and meaning |
| --- | --- |
| `CORE_API` | For a tool call by the response model, resolve `../../_shared/completion_assessment.py` from the directory containing this installed reference. The skill catalog supplies that absolute reference path. For explicit source-tree execution, use `_shared/completion_assessment.py` under the root of the checkout that the task names. Hooks use their registered runtime API. Each API loads its own deployment’s ledger and helpers; the calling shell needs no `GHOST_ALICE_CORE_ROOT`, `GHOST_ALICE_SKILLS_DIR` or `GHOST_ALICE_RUNTIME_SHARED_DIR`. A missing selected API is an installation error. |
| `LEDGER_ROOT`, `PLATFORM`, `SESSION_ID` | The `ledger_root`, `platform` and `session_id` fields of the current session metadata. After the hook runner reconciles a pending Claude Code input, `--read-intent` supplies `input_event_id`, as the ledger schema describes. |
| Current response route | The ready owned response route registered through task-router for that session metadata and the current input that Core code recorded: on Claude Code, an input whose transcript row carries the host's human-origin markers; on Codex, every prompt that Codex's prompt-submit event (`UserPromptSubmit`) delivers. A completion review selects exactly one owned work unit; when the response route selects several, pass its exact `--work-unit-id`. An explanation review binds an answer that carries no completion or partial completion block. |
| `ANSWER` | Absolute runtime scratch path that the human user's instructions and the project instructions do not prohibit and the host's permission rules allow, containing the complete proposed answer with its actual completion or partial completion block. The response model writes the draft through the host's file tool. It includes every paragraph it intends to submit; a proof extract does not cover omitted prose. |
| `ORIGINAL_SOURCE` | Absolute path to an original observation or source whose contents support the draft. Repeat `--source` for each distinct source. Use the host's existing record of the tool call or the original artifact without rerunning it or making another full copy. Preserve its locator/time; do not turn a later observation into historical proof. |
| `ORIGINAL_TIME` | The original timezone-bearing verification timestamp for a completion claim. Omit it for an explanation prepared from new source paths; retained completion proof keeps its original time. A later clock reading cannot supply a missing original time. |
| `CLAIMS` | For an explanation prepared from new source paths, an absolute scratch path that the human user's instructions and the project instructions do not prohibit and the host's permission rules allow, containing a JSON string array of the exact draft sentences or phrases the response model will verify. The response model selects these claims from the complete draft; Core code does not infer them from prose. Completion and partial completion candidates instead obtain their claims from the completion or partial completion block. |

If a required binding or original observation is unavailable, report that recording dependency as unresolved; do not infer a module root from the ledger directory. An authenticated explanation review that the human user explicitly requested needs the current response route but not an acceptance criterion of execution work or a previous completion record.

The response model runs the following form through the host's shell tool after binding every variable above. Each environment assignment applies to this process only. Repeat the same three assignments for `record-response`; do not change the host's thread identity.

```bash
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" prepare-response \
  --response-file "$ANSWER" --source "$ORIGINAL_SOURCE" --verified-at "$ORIGINAL_TIME"
```

For an explanation prepared from new source paths, write the selected exact draft statements as a JSON string array at `CLAIMS`, pass `--claims-file "$CLAIMS"`, and omit `--verified-at`. Supply every source-backed assertion requiring verification; neither an empty list nor a whole-response label substitutes for those claims. For retained completion proof, replace all new `--source` arguments with one `--reuse-candidate` reference to the original reviewed candidate and retain its verification time. An explanation of previously reviewed completion uses `--reuse-candidate` without `--claims-file`; it retains the original proof claim bindings and inherits the original verification time when `--verified-at` is omitted. Never combine newly read sources with historical reuse.

Core code saves an immutable candidate in the ledger that the session metadata names. The CLI result supplies `candidate_reference`, source IDs with their locators and digests, and `response_binding.binding_id`. It does not return source bodies or another copy of the answer; the caller retains the draft supplied through `--response-file`. Each `bindings[]` entry supplies a derived `binding_id` and `claim_index`; completion entries also identify the acceptance criterion by `id`, `work_unit_id` and `contract_revision`. The caller maps these coordinates to the claims and acceptance criteria it supplied, without copying their prose into another return. For retained proof whose original claim wording is needed, use the same validated source/session environment to invoke `python3 "$CORE_API" read-claims --candidate "$ORIGINAL_CANDIDATE" --index 0`, replacing the candidate and zero-based index with the selected original coordinates. This explicit lookup authenticates the original owner and returns only selected original bindings. Core code derives the IDs; the response model copies them from that result rather than constructing hashes or guessing names. The candidate retains authenticated original-source references. Preparation is storage, not a favorable judgment.

The response model then performs two distinct judgments in order:

1. Map each returned `bindings[].claim_index` to its authored or explicitly retrieved original claim, then compare it with the original sources and, for completion, its identified acceptance criterion. Use each returned source locator to read the passages needed for that claim. Preparation returns references, not source bodies; use the original bytes when text decoding is insufficient. Produce a separate judgment per binding with the inspected `source_ids`, supporting/contradicting/unavailable evidence and its implication. Finish this claim comparison before reviewing the answer as a whole; authenticated explanations that the human user explicitly requested require these recorded per-claim judgments too.
2. Use those completed comparisons, the complete draft supplied through `--response-file`, and the human user's current objective and constraints to judge the whole answer. Determine whether it supplies the requested result, respects scope, preserves unresolved acceptance criteria and avoids broader unsupported conclusions. Retain this separate judgment; agreement with individual sources alone does not establish whole-response adequacy.

Bind `REVIEW` to an absolute runtime scratch path that the human user's instructions and the project instructions do not prohibit and the host's permission rules allow, then use the host's file tool to write a JSON object with exactly `claims` and `response` at that path. The following is a shape example; copy every ID from the prepared candidate and replace the sample rationale with the actual comparison:

```json
{
  "claims": [{
    "binding_id": "<one bindings[].binding_id from prepare>",
    "verdict": "supported",
    "source_ids": ["<inspected sources[].source_id>"],
    "rationale": "The identified original observation supports this exact claim and acceptance criterion because ..."
  }],
  "response": {
    "binding_id": "<response_binding.binding_id from prepare>",
    "verdict": "supported",
    "source_ids": ["<inspected sources[].source_id>"],
    "rationale": "Using the completed claim comparisons, the complete answer satisfies the stated objective and constraints because ..."
  }
}
```

Supply one per-claim judgment for each returned binding, with no missing or duplicate bindings. Each judgment uses `supported | unsupported | unavailable`, a substantive rationale and the IDs of sources actually inspected; a supported factual claim requires inspected sources. For unsupported, identify the conflicting original or the answer passages that cannot both hold under the same subject, time, conditions and scope. An internal contradiction needs no invented external source ID. Use unavailable when the necessary evidence cannot be established. An authenticated explanation that the human user explicitly requested cannot use `claims: []` or place its per-claim judgments only inside `response.rationale`. The `response` object records the separately reached whole-response judgment. An explanation reusing a reviewed candidate retains its actual claim bindings and evidence under the reuse contract.

After both judgments, bind `REFERENCE` to the returned `candidate_reference` and invoke:

```bash
GHOST_ALICE_SESSION_INTENT_ROOT="$LEDGER_ROOT" \
GHOST_ALICE_PLATFORM="$PLATFORM" \
GHOST_ALICE_SESSION_ID="$SESSION_ID" \
python3 "$CORE_API" record-response \
  --candidate "$REFERENCE" --judgment-file "$REVIEW"
```

When files for `ANSWER` or `REVIEW` are prohibited but the human user's instructions and the project instructions do not prohibit Python execution and the host's permission rules let it run, import `completion_assessment` from the directory containing the selected `CORE_API` through the tool that runs Python. Call `prepare_response_review(source, response_text=<complete draft>, source_paths=<absolute original paths>, verified_at=<original time or None>, reuse_candidate=None, work_unit_id=None, claim_statements=None)`, supplying the selected exact draft statements as `claim_statements` only for an explanation prepared from new source paths. Leave that argument unset whenever a completion or partial completion block or a proof-reuse argument selects original proof bindings. For a retained completion explanation, use `reuse_candidate` as described above. Then call `record_response_review(source, candidate_reference, review)`. Construct `source` from the current host environment and bind the three `GHOST_ALICE_*` keys above to their established values. Do not fabricate omitted runtime keys. The other arguments have exactly the same origins and shapes as the CLI. This avoids draft/review scratch files, not source requirements or either judgment.

Core code validates schema, source integrity, owner/input/contract bindings and the submitted judgments, then atomically stores the per-claim judgments and the whole-response judgment in the ledger. It does not compare meaning or decide truth. Successful recording returns `status: reviewed` and assessment/response references without the answer body. The response model submits the unchanged reviewed draft through host output. An internal recording operation does not itself require a new status answer. Atomic storage does not combine the two judgments, and matching bytes do not prove semantic support.

Read both exit status and returned JSON. `status: unsupported` records the unfavorable judgment and does not authorize submitting that draft; correct the actual claim or answer before preparing a changed candidate. A nonzero result returns `status: unavailable`, `reason` and `detail`; correct the named missing/stale binding, source or schema only when a dependency of work that the human user's instructions authorize requires it. A prepared candidate remains reusable only while its draft, sources and binding match. Changed draft/source content requires a new preparation and review of affected claims plus the complete answer. Preserve immutable judgments, supported original proof and original observation times. An unchanged failure does not trigger another submission or running the work again.

The explanation review records the response model's judgments; it does not satisfy an acceptance criterion of the work.

When either judgment identifies previously undeclared work that the human user's instructions authorize and a required acceptance criterion depends on, the response model declares that work through the existing response route registration before delivering the answer. Follow the selected installed session-intent-analyzer's `references/ledger-schema.md`, section The response model's declared remaining work, for `declared_remaining_work`, its prerequisites and source references. An unmet criterion alone does not establish necessary work. Updating only this declaration preserves the recorded review when the current input, route boundary, selected criteria, original sources and answer remain unchanged; the response model does not repeat the judgments or execute the work again merely to update the declaration.

## Proof bytes and time

The main verification skill defines the schemas of the completion and partial completion blocks. Keep exactly one nonempty top-level `- evidence:` section in new proof. Core code reads the exact source-reference text from it; nested claim evidence cannot substitute. Missing, empty, duplicate, ambiguous or placeholder evidence remains rejected.

Supply the original raw timestamp through `--verified-at`; Core code retains that `verified_at` as audit data. Do not add or refine a timestamp in unchanged proof.

## Deliver

The response model retains a recording-only error; the error neither blocks Stop nor requests resubmission of the answer. Core code's Stop script compares the answer's text with the recorded review, when one exists, so a changed claim shows as a mismatch.

Do not invoke Stop to obtain a read-only status. For the definition of a current acceptance criterion, use the `session_intent_ledger.py --read-state --pointer /acceptance_criteria/<index>` procedure with the current session metadata. Reuse supplied host events when available; a new delivery lookup requires an inspection task that the human user's instructions authorize.

## Recording-only failures

Preserve the original proof of the work after a binding or recording failure. The response model first identifies whether the missing record prevents a required claim or a dependent task that the human user's instructions authorize. If it does not, retain the recording gap without another command or user-facing repair response. An unmet required recording condition stays unresolved.

When a required dependency needs correction, repair only that record: task-router for the current response route, Core code assessment for a per-claim judgment. Retain the original CLI result and reference. An identical unchanged failure suspends that attempt until a relevant state change. Do not fabricate origin, permission, admission, approval, run state or source authority.

If additional task files are prohibited, use stdin or runtime scratch that the human user's instructions and the project instructions do not prohibit and the host's permission rules allow rather than creating them.

Systematic-debugging owns [incident accounting](../../systematic-debugging/SKILL.md#incident-accounting-and-continuation); verification-before-completion owns [useful checks](../SKILL.md#evidence-selection-and-stop-gate); boundary-contract defines what the human user's instructions authorize and how the response model records its boundaries. Recording-specific suspension does not reset those limits.
