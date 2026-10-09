# session-intent-analyzer ledger schema
## Contents

- [intent-state.json](#intent-statejson)
- [intent-events.jsonl](#intent-eventsjsonl)
- [current-session.json](#current-sessionjson)
- [Hook Observation Receipt](#hook-observation-receipt)
- [Mutation Contract and Recovery](#mutation-contract-and-recovery)
- [Core Response Projection](#core-response-projection)
- [forbidden persistence](#forbidden-persistence)


## intent-state.json

`intent-state.json` describes the portable state shape. The authoritative store is `<ledger_root>/ghost-state.sqlite3`, keyed by `(platform, session_id)`. Runtime consumers call `read_session_state` or CLI `--read-state` with exact receipt coordinates. A missing or modified JSON export does not override an imported database session.

`ledger_revision` advances once per successful semantic/intake commit. State, its current input anchor, its ordered audit event and advisory discovery metadata commit in one SQLite transaction. Bound Autopilot writers pass the same explicit `transaction` connection to Core, so an outer rollback also rolls back completion evidence. Foreign keys bind events to their session; a supplied connection from another database root is rejected. JSON exports are requested explicitly with `--export-to <separate-root>` and must not overwrite original migration inputs.

Revision and input character count are integers from `0` through `2**53 - 1`, excluding booleans, so Python and JavaScript readers agree. All four anchor fields must be present together once any is present; partial new anchors are invalid and cannot fall back to historical events. A custom intake observation must match the raw input's digest and exact integer character count before any mutation or pending-audit recovery occurs.

Completion evidence belongs to the criterion's current definition. Re-recording the same ID and unchanged summary preserves verified `met` status, digest, and timestamp. A changed summary or explicit withdrawal of admission resets that criterion to `unmet` and removes its current proof fields; unrelated criteria retain their evidence. A wording change is conservatively treated as a changed condition because the parser cannot certify semantic equivalence. The existing `acceptance-criterion-met` event remains historical evidence, not proof for the revised condition. A raw delta cannot promote a condition to `met`; revalidation uses the normal completion-check path.

intent-state.json is update-plus-accumulate state. Scalar intent fields such as `current_goal` and `user_intent_summary` are updated to the latest value when a new semantic delta arrives. `constraints`, `non_goals`, `open_questions`, and `risk_flags` accumulate with value-based deduplication. Owned `acceptance_criteria` merge by `(work_unit_id, contract_revision, id)`; ownerless criteria merge by their stable `id`. Decisions merge by stable `id` and can mark prior decisions as superseded through `supersedes`. The database event table is an append-only audit log exposed through `--read-events` and explicit JSONL exports; the state file is not a transcript that appends raw prompts or old summaries.

`model_security_decision` is a compatibility audit field. Import and audit callers may record it under the exact-event mutation contract. Intake, routing, checkpoints and AP do not derive execution authorization or native tool permission from it.

```json
{
  "schema_version": "session-intent-ledger.v1",
  "platform": "codex",
  "session_id": "abc",
  "created_at": "2026-05-19T00:00:00Z",
  "updated_at": "2026-05-19T00:00:01Z",
  "current_goal": "current goal",
  "user_intent_summary": "compressed intent summary",
  "constraints": [],
  "non_goals": [],
  "open_questions": [],
  "acceptance_criteria": [
    {
      "id": "AC1",
      "summary": "verifiable completion criterion",
      "source": "user-explicit"
    }
  ],
  "decisions": [],
  "risk_flags": [],
  "model_security_decision": {
    "decision": "allow",
    "risk_flags": [],
    "reason": "model judgment summary, not raw prompt, maximum 240 characters",
    "input_event_id": "sha256:...",
    "recorded_at": "2026-05-19T00:00:01Z"
  },
  "consumer_hints": {},
  "conduct_feedback": []
}
```

`conduct_feedback` is the behavioral-correction signal. It records how the agent operated relative to what the user asked, not the task content. When the user corrects the agent's conduct, record a compressed lesson instead of the episode. Trigger cases include under-delivery, silent scope narrowing, reporting or asking instead of executing an explicit instruction, punting a decision the content could resolve, leaving an unrequested historical trace, a redundant check without an actual mutation path, and explaining a confirmed flaw in a provided deliverable without returning the corrected deliverable. Entries merge by stable `id`, but repeated same-id correction observations in one session must increment `occurrence_count` instead of disappearing. Status-only updates, such as marking a lesson `encoded`, do not increment `occurrence_count`. Each entry is `{ "id", "summary", "failure_pattern", "corrective_rule", "source", "status": "open | encoded", "occurrence_count" }`, where `source` is `user-explicit` when the user stated the correction and `status` becomes `encoded` once the lesson is reflected in a gate skill or memory. `summary` is the fallback human-readable lesson when a split `failure_pattern`/`corrective_rule` is not available. skill-evolution consumes this field to propose gate-skill updates. Store the reusable pattern only, never the raw prompt.

Each entry may also carry `pattern_class`, one stable failure family, so differently named corrections of the same failure recur visibly across sessions: `redundant-verification`, `missed-reverification`, `wrong-copy-evidence`, `unsupported-claim`, `restatement-loop`, `unrequested-content`, `under-delivery`, `unnecessary-reapproval`, `scope-widening`, `scope-narrowing`, `premise-mismatch-without-stop`, `governance-record-error`, `domain-rule`. A later update without the field keeps the recorded class. A present but unknown `source` or `pattern_class` rejects the whole write with one error that lists the allowed values; a missing `source` keeps the `user-explicit` default, a missing class stays unclassified, and stored entries are not re-validated.

The consumer snapshot includes `active_decisions`, a detached copy of the non-superseded decision objects, and `latest_scope`, a detached copy of the recorded scope object or `{}` when absent or malformed. `decision_count` remains the number of active decisions. These are semantic-context fields, not permission or completion evidence. The snapshot preserves accumulated constraints and non-goals and leaves `model_security_decision` unchanged. Recorded scope does not automatically supersede list entries or change explicit authority. Audit fields do not grant authority.

Snapshots also expose the persisted `platform`, `session_id`, `ledger_revision`, and `latest_input_event_id` from the same authoritative state read. Missing metadata is `null`, not an invented revision or input anchor. An explicitly persisted empty input ID means no input has been observed in that state. A read-only snapshot is still not an authorization check; mutation APIs perform strict validation.

Capture provenance is a semantic judgment, not a parser guarantee. `source: user-explicit` requires the user's asserted prior mismatch, including a clear contextual rejection; `source: inferred` requires an actually observed behavior gap. Prospective constraints, reminders, changed requirements, and refreshed information do not themselves prove an earlier mistake. Record those in the intent fields unless a mismatch is also supported. Do not invent an occurrence from hypothetical misconduct, repeated constraints, or rereading an existing lesson. The absence of raw history does not invalidate a correction the user actually asserts. Consult supplied context before attributing conduct, preserve only the compressed comparison, and leave unsupported feedback absent.

Do not invent the chronology of a boundary. A timestamp on a decision does not date an undated restriction; active status, admitted criteria, and reports of prior edits alone do not establish that the restriction was later replaced. Distinguish a current user instruction or supplied conversation/event evidence from a compressed current_goal label. A clear current instruction can authorize a bounded change without formal revocation wording. If only conflicting snapshot fields are available, keep that conflict unresolved rather than calling one field initial or outdated without evidence. Ask about the contested authority only; missing task files are a separate issue.

## intent-events.jsonl

`intent-events.jsonl` is a portable event export. Runtime readers use `--read-events`; events record no raw prompts.

```json
{"ts":"2026-05-19T00:00:00Z","event":"user-input-observed","platform":"codex","session_id":"abc","input_digest":"sha256:...","input_char_count":12,"delta_keys":["current_goal"]}
```

When an event accompanies raw user input, it is `user-input-observed` and includes `event_id` (the observation's `input_event_id`) and `input_digest`. An agent-only delta is `intent-updated`: its `input_event_id` references the applicable observation but does not create or displace an input event. Each event written by this API includes `ledger_revision` and a unique `mutation_id`. Imported events without these fields remain readable.

`semantic_changes` records before/after projections of changed goals, intent summaries, constraints, non-goals, open questions, acceptance criteria, decisions, conduct feedback, and scope. It is derived from normalized state rather than the raw delta. Nested entries use explicit key allowlists; scope preserves `allowed`, `prohibited`, `non_goals`, `constraints`, and `stop_conditions`. Unknown accessory fields, security reasons, consumer hints, raw prompts, and tool-output fields are excluded. This makes intermediate scalar corrections and criterion reopening inspectable without copying arbitrary request objects. Callers must still supply compressed semantic summaries: the code does not detect raw quotations disguised inside a valid summary field. Historical digest-only events are not reconstructed or assigned invented chronology.

## current-session.json

`current-session.json` is an advisory discovery pointer for each platform at `.tmp/session-intent/<platform>/current-session.json`. Writers update SQLite discovery metadata atomically; the pointer cannot select a write identity.

```json
{
  "schema_version": "session-intent-current.v1",
  "platform": "codex",
  "session_id": "abc",
  "state_path": ".tmp/session-intent/codex/abc/intent-state.json",
  "updated_at": "2026-05-19T00:00:01Z"
}
```

This file does not include raw prompts. Read-only consumers may use it for discovery. It is shared across sessions and cannot supply identity to an intake hook or mutating CLI call.

## Hook Observation Receipt

After a successful digest-only observation, the hook includes `[session-intent-receipt]` JSON in `hookSpecificOutput.additionalContext` with `hookEventName: UserPromptSubmit`. The message also appears in `systemMessage` for host display, but that field alone is not the model context channel. Text output includes the same message. This context receipt is not a new persisted ledger file. Wrappers must preserve the machine JSON and additional context independently of user display verbosity; an audit log of intended model output alone does not prove model delivery. See the [official Codex hooks output contract](https://learn.chatgpt.com/docs/hooks#userpromptsubmit).

```json
{
  "schema_version": "session-intent-observation-receipt.v1",
  "intake_status": "observed",
  "ledger_root": "/absolute/ledger-root",
  "storage_backend": "sqlite",
  "database_path": "/absolute/ledger-root/ghost-state.sqlite3",
  "platform": "codex",
  "session_id": "abc",
  "state_path": "/absolute/ledger-root/codex/abc/intent-state.json",
  "events_path": "/absolute/ledger-root/codex/abc/intent-events.jsonl",
  "input_event_id": "sha256:..."
}
```

Paths and safe platform/session components come from that successful `record_turn` result, and `input_event_id` comes from the same observation. Receipt construction does not reread `current-session.json`; concurrent pointer changes must not redirect a semantic update. The receipt contains no raw input or semantic body. Empty-input and degraded hook paths emit no observation receipt.

Semantic writers pass the receipt coordinates explicitly using `--root`, `--platform`, `--session-id`, and `--expected-input-event-id`. The expected ID must be the receipt used to make the decision, not a fresh ID substituted onto an old decision. Mutating CLI calls bind to native `CODEX_THREAD_ID` for platform `codex`, then to `GHOST_ALICE_SESSION_ID` when no native binding exists. A conflicting explicit id fails before any file write. Without a binding, `--session-id` is required; a mutable shared pointer cannot select a write destination. Hook intake uses payload identity, then environment identity, and degrades without an observation receipt if both are absent. Explicit historical read-only snapshots and the library's explicit-session `record_turn` API remain available for adapters and offline inspection. This guard prevents accidental session mixing; it is not a security sandbox against a caller that can change its environment or write files directly.

The receipt does not grant write access, clear security decisions, or prove semantic completion. A write failure must be reported rather than bypassed through another root, migration, altered host identity, or increased permissions.

## Mutation Contract and Recovery

All supported state mutations use `record_turn`, `mark_acceptance_criterion_met`, or `record_response_route`. Each validates exact canonical platform/session identity, opens an immediate SQLite transaction, compares the expected input binding and merges state with its audit event. Semantic/completion writers also accept an optional expected revision. Unsafe identifiers are rejected rather than sanitized into another identity. The root `.sqlite-authority.json` marker records cutover before the first semantic commit, so a lost database requires explicit restoration instead of recreating state from stale JSON. Corrupt databases and unsupported schema versions fail closed; they never reactivate stale JSON exports.

`record_turn(..., expected_input_event_id=receipt_id, expected_revision=revision)` requires the expected input ID whenever an observed input is active. A digest-only new-input intake creates an anchor and is exempt from that comparison; attaching a semantic delta to new raw input does not bypass the check. `expected_revision` additionally fences decisions made against a whole-state snapshot. Independent additions for the same input can serialize without requiring a common revision. Audit fields do not supply routing or native permission authority.

`mark_acceptance_criterion_met` additionally requires the approved `expected_criterion` with exact `id`, `summary`, `source`, and boolean `admitted`. Changed criterion coverage requires affected verification. The direct met writer is input-bound; managed v2 can bind a current request to accepted unchanged proof while retaining its original source and verification time. The writer validates digest syntax and binding; the caller must validate the actual completion evidence. A raw delta cannot assert completion.

`storage_transaction(root)` yields a connection; pass `transaction=connection` to nested Core calls. Only its owner commits or rolls back. Nested mutations use savepoints, so catching a failed nested operation cannot commit half that mutation. Connections are operation-local. SQLite uses WAL, foreign keys, FULL synchronization and a ten-second contention wait; this wait is not a limit on task duration. Independent roots use independent databases. No exactly-once claim is made for an unversioned caller deliberately submitting the same delta twice.

`migrate_session` and first mutation validate a JSON session and import its normalized state plus ordered events atomically. A pending audit event is reconciled once in that transaction. Source files remain unchanged and their hashes are recorded. Repeated import returns the existing database state; a corrupt source record creates no partial session. Quiesce source writers before import and use only authoritative database writers afterward. Retain source files for provenance inspection; copying JSON over database authority is not a supported recovery operation.

`read_session_state(..., recover_audit=False)` is read-only. Unimported JSON state preserves absent metadata rather than fabricating proof of intake, including a pending audit marker if present. Audit fields remain visible and do not grant permission. `recover_audit=True` explicitly imports and reconciles validated JSON state. `read_session_events` returns ordered authority; `list_session_states` includes database sessions and unimported JSON identities. API failures must be reported, not converted into a successful empty snapshot. `load_state` and `consumer_snapshot` resolve canonical compatibility paths through the same database authority.

For live database backups use SQLite's backup API or a quiescent, consistent database copy including its journal state; copying only an active main database file can omit committed WAL data. Explicit JSON exports are portable inspection artifacts, not transactional backup or runtime authority. Stored summaries do not establish semantic truth and these guards do not protect against arbitrary direct database tampering by a process with write access.

## Core Response Projection

Core owns `response-route.v1` validation and persistence. task-router supplies the current semantic route candidate; session-intent-analyzer owns semantic criterion updates and admission facts. Neither an addon nor an intake hook guesses current selection from the accumulated backlog. Intake creates no projection: a new input marks the retained route `stale`, preserves the stable work-unit registry, and leaves prior `met` evidence intact. Missing projection is an explicit absence, not a bootstrap, intake, or security denial.

The ledger stores `work_units` as a registry of immutable `(work_unit_id, contract_revision)` contracts and `response_route` as the current projection. A unit has an exact canonical `work_unit_id`, `contract_revision` integer from `1` through `2**53 - 1` excluding booleans, `kind: execution | nonexecution`, boolean `admitted`, and `owner: {platform, session_id}` equal to the authoritative session. Registry entries additionally keep `criteria: [{id, summary, source}]` to fence definition changes. Same-revision kind, owner, or criterion-definition changes fail; an explicit newer revision is required. Admission can change with authority without changing business evidence. A current projection cannot revive an older revision after a newer one was registered.

Each owned criterion carries both `work_unit_id` and `contract_revision`. A partial or malformed owner pair rejects the delta. Ownerless entries remain unbound and cannot enter a ready selection; a repeated local ID does not acquire another entry's owner. The sole `met` writer uses the approved snapshot's exact owner pair to select a local ID and requires owned criteria to be selected in a ready current route. Direct ownerless writes use the same input-bound API. Raw deltas cannot assert `met`. Changing a business condition reopens only that condition; refreshing a route does not erase its proof.

Candidate shape, with no accessory keys:

```json
{
  "schema_version": "response-route.v1",
  "routing_surface": {
    "intent_relation": "continuation", "primary_request": "implement the approved contract",
    "causal_axis": "n/a", "response_mode": "normal",
    "response_order": "imperative-result-first-after-necessary-work",
    "change_depth": "structural", "focus_layer": "meso",
    "verification_complexity": "level-2", "boundary_contract": "required",
    "forced_visibility": "no", "reason": "continuing authorized work"
  },
  "work_units": [{"work_unit_id": "repair", "contract_revision": 1,
    "kind": "execution", "admitted": true,
    "owner": {"platform": "codex", "session_id": "abc"}}],
  "selected_work_unit_ids": ["repair"],
  "selected_criteria": [{"work_unit_id": "repair", "contract_revision": 1, "criterion_id": "AC1"}]
}
```

The routing enums are exactly the task-router surface enums, with underscores replacing field-name hyphens. All eleven surface fields are required. `primary_request`, `causal_axis`, and `reason` are compressed nonempty summaries of at most 500 characters. Unknown schemas/enums/fields, duplicate references, inconsistent owners, unadmitted selected units or criteria, and unbound references fail with `RouteSchemaError` (`code: route-schema`). The projection selects only the current response's units and criteria; selection may be empty for an ordinary reply. A unit/response-mode label cannot exempt an actual closure claim from completion evidence.

The validated projection adds `status: ready`, authoritative `platform`, `session_id`, `input_event_id`, and the caller's `boundary_ref` (canonical identifier or `null`). A required boundary needs a nonnull reference. `selected_criteria` contains detached ledger criterion snapshots with `criterion_id` as the local-ID alias. These snapshots freeze the condition selected at registration; current status/proof remains the origin ledger's authority. The read API adds `current_input_event_id`, returns `stale` when the stored event or selected definition no longer matches current state, and rejects malformed stored schemas. A changed input reopens its route/authorization binding, not unrelated business verification.

Supported APIs exist in `_shared/response_route.py` and as keyword-only methods in `session_intent_ledger.py`:

```python
record_response_route(root, platform, session_id, expected_input_event_id,
                      candidate, boundary_ref, *, transaction=None)
read_response_route(root, platform, session_id, *,
                    expected_input_event_id=None, transaction=None)
```

`record_response_route` returns the validated ready projection. Core compares the exact current input event inside the same immediate SQLite transaction that writes registry, projection, revision, and `response-route-recorded` audit event. Missing/stale input bindings raise `RouteBindingError` (`code: route-binding`). The audit event stores a projection digest and selected identity references; no business answer body is accepted. `record_turn` cannot write route/registry fields through a semantic delta. `read_response_route` reads only the canonical SQLite session row and returns `None` without creating storage when that row is absent; it never falls back to compatibility JSON for a route. Pass the same existing `storage_transaction(root)` connection to both route read and origin-state read for an atomic consumer snapshot; nested writes retain savepoint/outer rollback behavior.

`canonical_intakes` is the separate canonical SQLite intake record, keyed by `(platform, session_id)` and storing `input_event_id`, `input_digest`, `input_char_count`, and `event_sequence`. Its foreign key targets that session's authoritative audit event. Native intake commits this record atomically with state and event. Route consumers validate the exact current owner, event, digest, count, and sequence against SQLite; a copied JSON `canonical_input_event_id` is only a projection and never authorizes intake. Generic older JSON import cannot manufacture the separate record, even if it preserves copied ready/stamp fields.

A SQLite receipt without canonical provenance can register without another user observation only when its exact current event, digest, count and owner match canonical audit evidence. A validated native intake record supplies the authoritative sequence boundary. If no such record exists, the import source files must match the hashes in SQLite `legacy_migrations`; Core reconstructs and compares the exact imported audit prefix, including a pending audit event, and requires the current observation to follow that prefix. These attested sources establish import provenance, not current route or criterion authority. Registration persists the canonical record atomically. Missing or changed import sources without a trusted native boundary leave provenance unresolved and produce a binding error; restore boundary evidence rather than fabricate input. Import clears copied projection stamps and stales imported routes. Canonical intake establishes its record directly without reopening business proof.

CLI: `_shared/response_route.py --root <receipt-root> --platform <receipt-platform> --session-id <receipt-session> --expected-input-event-id <receipt-event> --boundary-ref <boundary-id> --candidate-json <candidate-json>` registers; replace mutation arguments with `--read` to inspect. Explicit session identity is required, mutating calls reject a conflicting native host session, and reads may inspect an explicit historical session. Success emits JSON; a typed error emits `{error, message}` on stderr and exits nonzero. Compatibility JSON exports remain inspection artifacts, never route authority. No-work terminal routes use only an already supplied internal host handoff and otherwise leave the projection absent; they do not open tools or probe user state to manufacture a projection.

## forbidden persistence

- raw user prompt
- raw system/developer instructions
- raw tool output
- raw credential, token, API key, or private key values
- raw external URL fetch results
