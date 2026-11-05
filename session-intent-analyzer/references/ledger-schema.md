# Intent Ledger Schema

This reference defines the intent ledger's stored state, mutation bindings, provenance and the Core response projection. Runtime consumers use the public ledger API. Portable JSON supplies explicit import/export data; the session SQLite database is runtime authority.

## State and transaction authority

The database is `<ledger_root>/ghost-state.sqlite3`, keyed by exact `(platform, session_id)`. A successful intake or semantic commit advances ledger_revision once and commits the state, input anchor, ordered event and advisory discovery metadata in one SQLite transaction. A writer that joins the same unit of work passes the same explicit transaction connection to Core code; an outer rollback also rolls back completion evidence. Foreign keys bind events to the session, and a connection from another database root is rejected.

Revision and input character count are integers from zero through `2**53 - 1`, excluding booleans. The input anchor consists of `ledger_revision`, `latest_input_event_id`, `latest_input_digest` and `latest_input_char_count`. Once any anchor field exists, all four must exist; a partial anchor cannot fall back to historical events. A custom intake observation must match the raw input's digest and exact character count before mutation or pending-audit recovery.

The response model requests portable exports with `--export-to <separate-root>` and must not overwrite original migration inputs. A missing or modified export cannot override an imported database session.

## Routing input and selective lookup

`--read-intent` returns the current `routing_keywords`, `routing_summary`, input identity, revision and detail pointers. Keywords select procedures; they do not replace proof. The response model uses `--read-state --pointer /constraints/0` (or another returned pointer) only when that detail is needed. `--expected-input-event-id` and `--expected-revision` reject a stale lookup. Unqualified `--read-state` returns the same compact references as `--read-intent`. Use `--export-to` when an authorized diagnostic requires a complete file export; the CLI returns its paths.

`keywords_input_event_id` identifies the input that supplied the keywords, so an unchanged retained keyword is not mistaken for a fresh interpretation.

## intent-state.json

The Python ledger writer replaces scalar goal/summary fields with the latest semantic delta. A supplied `constraints`, `non_goals`, `open_questions`, `risk_flags`, `routing_keywords` or `consumer_hints` value replaces that field as a whole, so an empty list or object clears it. Omitted fields remain unchanged. Owned acceptance criteria merge by `(work_unit_id, contract_revision, id)`; ownerless acceptance criteria merge by stable ID. Decisions merge by stable ID and may supersede prior decisions with `supersedes`. The state is not a transcript.

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
  "acceptance_criteria": [{
    "id": "AC1",
    "summary": "verifiable acceptance criterion",
    "source": "user-said",
    "admitted": true
  }],
  "decisions": [],
  "risk_flags": [],
  "consumer_hints": {},
  "conduct_feedback": []
}
```

An acceptance criterion row holds `id`, `summary`, `source`, `admitted` and the owner pair. The row holds no proof; the response review holds the judgments about the criterion. Recording the same ID and owner pair replaces the summary and source, and admission changes only through an explicit boolean or a contract-bound source. A response route that selected the earlier row no longer reads as ready. A wording change counts as a changed acceptance criterion because the parser cannot establish semantic equivalence. A semantic update that records acceptance criteria rewrites every row with these fields only. A stored criterion field the ledger does not define carries no meaning and never blocks a read or a write.

## Conduct and consumer snapshots

A conduct entry contains ID, summary, failure_pattern, corrective_rule, source, status (`open | encoded`) and occurrence_count. Summary supplies the readable lesson when the split failure/correction fields are absent. A supported repeated same-ID correction increments the count; status-only updates do not. Encoded means the lesson is reflected in a gate skill or approved memory, not that the original task is complete.

Optional pattern_class groups recurring failures across differently named entries:

`redundant-verification | missed-reverification | wrong-copy-evidence | unsupported-claim | restatement-loop | unrequested-content | under-delivery | unnecessary-reapproval | scope-widening | scope-narrowing | premise-mismatch-without-stop | governance-record-error | domain-rule`.

An update omitting pattern_class preserves the stored class. Unknown supplied source or class rejects the entire write with allowed values; absent source defaults to user-said, absent class stays unclassified, and stored entries are not revalidated. The ledger stores a supplied source label as given and never rewrites a stored one. A legacy `user-explicit` or `inferred` label stays valid; admission, route validation and skill-evolution read it as `user-said` or `interpreted-as`. That parser default does not establish provenance: the response model follows the correction evidence rules in `../SKILL.md`.

`consumer_snapshot` returns detached active non-superseded decision objects, latest_scope or {} when absent/malformed, and decision_count. It returns the current constraints and non-goals. List-field updates follow [intent-state.json](#intent-statejson). The response model must reconcile these facts with human instructions; scope presence, timestamps, admission and previous edits do not independently establish authorization or restriction chronology.

Snapshots also expose persisted platform, session_id, ledger_revision and latest_input_event_id from the same read. Missing metadata is null rather than invented. An explicitly persisted empty input ID means no input was observed in that state. A read-only snapshot is not an authorization check; writers validate bindings.

## Events and discovery

The database event table is append-only and available through `--read-events` and explicit intent-events.jsonl export.

An event accompanying raw input is user-input-observed and contains event_id equal to the observation's input_event_id plus input_digest. A delta that the response model submits without raw input is intent-updated: it references the input_event_id without creating/displacing an input event. API-written events include ledger_revision and unique mutation_id; older imported events without them remain readable. Claude Code intake appends native-input-staged, native-input-resolved, native-input-superseded with its cause, native-input-waiting once per blocking input and cause, and native-input-notice once per session and cause; none stores input text.

The Python ledger writer derives semantic_changes as before/after digests of normalized state for goals, summaries, constraints, non-goals, questions, acceptance criteria, decisions, feedback and scope. Nested allowlists retain only defined keys; scope retains allowed, prohibited, non_goals, constraints and stop_conditions. Accessory fields, hints, raw prompts and tool output are excluded. Callers still must provide compressed summaries: the parser cannot detect a raw quotation disguised as a valid summary. It does not reconstruct historical digest-only dialogue or invent chronology.

The shared pointer `current-session.json` is advisory:

```json
{
  "schema_version": "session-intent-current.v1",
  "platform": "codex",
  "session_id": "abc",
  "state_path": ".tmp/session-intent/codex/abc/intent-state.json",
  "updated_at": "2026-05-19T00:00:01Z"
}
```

Read-only consumers may use it for discovery. Writers use the exact session metadata or the session that the host reports, never this shared pointer. SQLite discovery metadata commits with state; the pointer itself is not write authority.

## Session metadata from the hooks

The session-start hook delivers the session metadata in its fixed form, which identifies the session and holds no input ID. The host repeats session start after resume, clear, compaction and fork, so one current copy stays in model input. The hook records the digest of that session metadata for the session in `session-checks.sqlite3` under the ledger root. While the recorded digest matches the current session metadata, the input hook records each input and adds nothing to model input. A code update that builds the same session metadata therefore adds nothing. When the record holds another digest, the input hook delivers the current session metadata once and updates the record. An input never creates the store or a record, because only a session whose start hook runs gets the session metadata back after compaction. When the session has no record, or the update fails, the input hook supplies the session metadata in its per-input form, described below, with that input.

```json
{
  "schema_version": "session-intent-session-receipt.v1",
  "ledger_root": "/absolute/ledger-root",
  "platform": "codex",
  "session_id": "abc",
  "state_path": "/absolute/ledger-root/codex/abc/intent-state.json",
  "events_path": "/absolute/ledger-root/codex/abc/intent-events.jsonl",
  "database_path": "/absolute/ledger-root/ghost-state.sqlite3",
  "storage_backend": "sqlite"
}
```

Codex intake commits the canonical digest observation directly. Without delivered session metadata, it places the session metadata JSON in hookSpecificOutput.additionalContext with hookEventName UserPromptSubmit. It also supplies systemMessage for display and equivalent text output. Claude Code intake stages the input until the trusted actor reconciliation in [Canonical intake provenance](#canonical-intake-provenance) resolves it, and unresolved staging supplies no canonical session metadata. The hook runner preserves machine JSON and model input at every visibility level. A log of intended output does not prove model delivery. The upstream Codex output contract is the [Codex hooks reference](https://learn.chatgpt.com/docs/hooks#userpromptsubmit).

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

Both forms of the session metadata carry `context_selection` when the session runs through the context gateway: the session-start hook adds it to the fixed form, and the input hook adds it to the per-input form it supplies when the session has no start record or the record update fails. The field holds `native_provider` (the gateway's provider name), `procedure` (the absolute path of the installed `_shared/context_lifecycle.md`) and `native_dir` (the host's configuration directory: the Claude Code directory the gateway records, or the Codex home). It is provider metadata: it grants no permission and does not show that a selection was applied.

All canonical `ledger_root`, `platform`, `session_id` and path fields and the event ID come from the same successful observation, without rereading the shared pointer. Pending Claude Code session metadata carries `ledger_root`, `platform` and `session_id` without an input_event_id; after the hook runner's reconciliation, --read-intent supplies input_event_id for subsequent expected-input comparisons. The session metadata has no raw or semantic body and is not a separately persisted ledger file. Empty-input or degraded paths emit no session metadata.

Semantic writers pass `ledger_root`, `platform` and `session_id` from the session metadata and the canonical input-event ID used for the decision, obtained directly from observed session metadata or from the authoritative state after trusted Claude Code reconciliation. A new ID cannot be substituted onto an old judgment. Codex mutation binds to the host's CODEX_THREAD_ID, then GHOST_ALICE_SESSION_ID when the host reports no session; a conflict fails before a write. Without either binding, the caller explicitly selects session-id. Hook intake uses payload then environment identity and degrades when both are absent.

Explicit historical read-only snapshots and the library's explicit-session record_turn remain available for adapters/offline inspection. The guard prevents accidental session mixing; it is not a sandbox against a process that can change its environment or write storage directly. The session metadata proves no semantic update. The response model reports a write failure instead of changing root, identity or permissions to bypass it.

## Mutation Contract and Recovery

Core Python APIs expose record_turn, bind_legacy_acceptance_criteria and record_response_route. Each validates canonical identity, opens an immediate SQLite transaction, compares expected input and writes state plus audit event. Semantic writers optionally compare an expected revision; legacy binding requires one. Unsafe IDs fail rather than being sanitized into a different identity.

The .sqlite-authority.json cutover marker is written before the first semantic commit. A lost database requires explicit restoration; corrupt/unsupported databases fail closed instead of reactivating stale JSON.

`record_turn(..., expected_input_event_id=input_event_id, expected_revision=revision)`, with the `input_event_id` that `--read-intent` returns, requires the expected event when an observed input is active. Digest-only new-input observation creates an anchor and is exempt from that comparison. Attaching a semantic delta to raw input does not bypass it. Independent additions for the same input can serialize without sharing a revision; a whole-snapshot decision uses expected_revision.

`storage_transaction(root)` yields an operation-local connection. Nested calls receive that connection; only its owner commits/rolls back. Savepoints prevent a caught nested error from committing half a mutation. SQLite uses WAL, foreign keys, FULL synchronization and a ten-second contention wait, which is not a task-duration limit. Independent roots use independent databases. Repeated unversioned identical deltas carry no exactly-once guarantee.

Migration validates and atomically imports normalized JSON state plus ordered events, reconciling a pending audit event once. It preserves source files and records hashes. Reimport returns existing database state; corrupt sources create no partial session. The operator quiesces source writers before migration and uses database writers afterward. Copying JSON over SQLite is not recovery.

`read_session_state(..., recover_audit=False)` and `--read-state` are read-only; they do not reconcile staged input with the transcript. Unimported JSON preserves missing metadata and pending markers instead of manufacturing intake proof. recover_audit=True explicitly imports/reconciles validated JSON. read_session_events returns ordered authority; list_session_states includes database and unimported JSON identities. load_state and consumer_snapshot resolve compatibility paths through the authority API. Callers report API failures rather than returning a false successful empty snapshot.

For live backup, the operator uses SQLite backup or a quiescent consistent copy with journal state. Copying only a live main file may omit WAL commits. JSON exports are inspection artifacts, not transactional backups. Stored summaries do not prevent tampering by a process with write access.

## Core Response Projection

The response model applies task-router to form the current semantic route candidate, then invokes Core code's response_route.py through a host tool. That Python code validates and persists the candidate. The response model also follows session-intent-analyzer to express acceptance criteria and admission facts; the ledger writer persists them. Intake hooks do not infer current selection from a backlog.

A new user input or an SDK-started turn marks the retained route stale and preserves stable work units. Missing projection is an explicit absence, not denial of bootstrap or intake. Consumers read the ready projection for current selection and the origin ledger for the current acceptance criteria; an export or registry entry replaces neither.

An answer without a completion or partial completion block retains continuing selected work without a new completion submission. When the user has requested an authenticated review of such an answer, the recorded review binds that exact text, and Core code's Stop script compares the answer with that record. The response model explicitly routes and verifies new completion claims under verification-before-completion.

A direct answer or a confirmation question follows the common contract without ledger access or route registration by the response model. Hook observation still stales the prior projection, and semantic persistence waits for the next authorized normal route. These restrictions do not prohibit authorized repair of Core code records on a normal route.

The work_units registry freezes contracts by `(work_unit_id, contract_revision)`. Revision is an integer from one through `2**53 - 1`, excluding booleans. Each unit declares kind execution/nonexecution, boolean admitted and exact owner platform/session. Stored acceptance criteria freeze ID, summary and source. Same-revision kind, owner or definition changes fail; a newer revision is required. Admission may change with authority. Selection cannot revive an older revision.

Each owned acceptance criterion carries both work_unit_id and contract_revision; a partial/malformed pair rejects the delta. Ownerless rows cannot enter ready selection or inherit ownership from a matching local ID.

```json
{
  "schema_version": "response-route.v1",
  "routing_surface": {
    "response_mode": "normal",
    "boundary_contract": "required",
    "reason": "continuing authorized work"
  },
  "work_units": [{
    "work_unit_id": "repair", "contract_revision": 1,
    "kind": "execution", "admitted": true,
    "owner": {"platform": "codex", "session_id": "abc"}
  }],
  "selected_work_unit_ids": ["repair"],
  "selected_criteria": [{
    "work_unit_id": "repair", "contract_revision": 1, "criterion_id": "AC1"
  }]
}
```

The routing_surface requires response_mode (`normal | direct-response | clarification-only`) and boundary_contract (`required | n/a`). The optional reason is a compressed nonempty summary of at most 500 characters. Unknown fields/schema/enums, malformed supplied values, duplicate references, inconsistent owners, unadmitted selection and unbound references raise RouteSchemaError with code route-schema. The optional reason does not relax current input, boundary reference, owner, admission or acceptance criterion and source validation.

Selection covers only this response and may be empty. Unselected execution units may remain without activating work. An invalid route does not exempt an actual completion or partial completion claim from proof.

The validated projection adds ready status, authoritative identity/input and canonical boundary_ref or null. A required boundary needs a nonnull reference. Selected criteria are detached snapshots with criterion_id as the local-ID alias; they freeze the selected acceptance criterion. The reader adds current_input_event_id and returns stale for changed input/definition, rejecting malformed stored schemas.

### Current necessary continuation feedback

When authorized continued work needs a next input, the response model declares its current necessity within the already required route registration. The optional `continuation_feedback` uses this shape:

```json
{
  "schema_version": "continuation-feedback.v1",
  "control": "active",
  "required_unprocessed": [{
    "criterion_refs": [{"work_unit_id": "repair", "contract_revision": 1, "criterion_id": "AC1"}],
    "reason": "The unresolved comparison still needs the current original evidence.",
    "next_information": "Compare the selected original passage with the changed requirement.",
    "prerequisite": "ready",
    "source_refs": [{"candidate_reference": "<existing Core code candidate reference>", "source_id": "<its original source ID>"}]
  }]
}
```

This is an illustrative declaration, not execution authority. `control` is `active | human-stop`; `prerequisite` is `ready | missing`. Each row has nonempty model-supplied `reason` and `next_information`, and nonempty selected, admitted owned criterion references. Core's Stop notice shows each row's `reason` and `next_information` to the user, so the response model writes them in the user's language. Rows cannot overlap. `source_refs` is an explicit list and may be empty when current information needs no original lookup. Absence leaves necessity undeclared; an explicit empty `required_unprocessed` list declares no currently necessary follow-up and does not establish that a selected acceptance criterion holds. A selected acceptance criterion alone establishes no necessity.

The canonical route writer authenticates each `(candidate_reference, source_id)` selector against an existing immutable candidate in the exact owning root and platform/session, verifies its original, and stores only authenticated source-reference metadata. A source ID is local to its candidate. Inline originals remain in their existing Core code record; feedback copies neither text nor bytes. A fabricated, changed or unavailable original rejects the declaration and rolls back the route transaction. Current input and the declared boundary continue to bind the normal route.

The reader revalidates referenced originals without rewriting the stored declaration. An affected row receives a read-only `continuation_feedback_issues` entry `{pointer, issue}`; its pointer is `/continuation_feedback/required_unprocessed/<index>` and its issue is `source-unverified | source-owner-unbound | source-changed | source-unavailable`. Consumers hold that row and can continue unrelated approved groups. The declaration contents and row indices stay unchanged, so the existing `response-route-recorded` event remains the original source. A consumer computing its event digest excludes only read-added `current_input_event_id` and `continuation_feedback_issues`. These metadata fields cannot be supplied to the route writer. A later human input stales the route and its feedback together.

Core code records this authenticated current declaration and schedules none of the work it names. The declaration carries each whole necessity row with its frozen boundary, allowed surfaces, dependencies and current prerequisite; recording it proves neither that the work was requested nor that it ran.

### APIs and CLI

```python
record_response_route(root, platform, session_id, expected_input_event_id,
                      candidate, boundary_ref, *, transaction=None)
read_response_route(root, platform, session_id, *,
                    expected_input_event_id=None, transaction=None)
bind_legacy_acceptance_criteria(*, root, platform, session_id, work_unit_id,
                                contract_revision, expected_input_event_id,
                                expected_revision, expected_criteria,
                                transaction=None)
```

The route writer compares current input and atomically writes registry, projection, revision and the response-route-recorded event. Missing/stale binding raises RouteBindingError with code route-binding. That event stores a projection digest and selected identities, never the answer. Semantic deltas cannot write route/registry fields.

The reader uses only the canonical SQLite row, returning None without storage creation when absent; it does not read compatibility JSON for routes. Consumers pass one storage_transaction connection to route and origin-state reads for an atomic snapshot. Nested writes retain savepoint/outer rollback behavior.

The CLI is `_shared/response_route.py --root <ledger_root> --platform <platform> --session-id <session_id> --expected-input-event-id <input_event_id> --boundary-ref <boundary-id> --candidate-json <candidate-json>`, where `<input_event_id>` is the `input_event_id` that `scripts/session_intent_ledger.py --read-intent` returns. Use --read instead of mutation arguments for inspection. Explicit identity is required; mutation rejects an identity that conflicts with the session that the host reports, while reads may target explicit history. Success returns JSON; a typed error returns {error, message} on stderr with nonzero exit. The response model interprets a stale/schema failure and repairs the affected binding, rather than fabricating a ready result.

### Canonical intake provenance

canonical_intakes is a separate SQLite record keyed by platform/session with input_event_id, input_digest, input_char_count and event_sequence. A foreign key points to the authoritative audit event. Codex direct intake or Claude Code's trusted actor reconciliation commits human input with state/event. Claude Code's UserPromptSubmit hook calls stage_native_input(), storing the input digest, character count, the host's prompt ID and the transcript's append boundary in pending_native_inputs. hook_profile_gate._reconcile_native_intake() runs after registered binding/hash validation and before the PreToolUse or Stop child, calling reconcile_native_inputs(). The pure native_transcript.capture_append_boundary(), locate_staged_input() and native_input_actor() helpers supply correlation, a waiting reason and actor evidence. Host marker values stay in native_transcript; the ledger receives only the actor, the status and an opaque marker shape. One reconcile reads each transcript once. The reconciler advances native_input_cursors; only a human result, including input typed while the response model works, reaches the shared _commit_turn() canonical writer. Staging does not write canonical authority. An SDK-started turn or an app event delivered while the response model works becomes non-human context; only an SDK user row also becomes a context intake original and closes human authority. An app event, a task notification from the host or a delegate's hand-back retains the last human input and route. A hook prompt can match the complete transcript text or one unique complete plaintext block beside Desktop host guidance; the match removes no sibling and still requires positive actor metadata in the transcript. A later located human input supersedes each earlier unknown or unlocated input, whichever row it lacks or file it names, and Core code retains their staging events and explicit native-input-superseded links with the cause, without promoting unknown actors. A notification or a non-human context input supersedes nothing; without a later located human input the queue waits. The hook runner passes record_wait because PreToolUse and Stop run after the host appends the turn's input; it records each wait once per blocking input with a cause key: row-not-found, row-ambiguous, transcript-unavailable, or unknown-actor with the marker shape. A generation-boundary reconcile can precede the append and records no wait. claim_native_input_notice() lets the hook runner show one notice per session and cause; it reads before it writes, so a hook without a due notice takes no write lock. list_native_input_waits() and `--list-native-waits` supply the list that the doctor shows. Text that UTF-8 cannot encode equals no staged input and stays out of correlation. When reconcile raises, the hook runner records the failure with record_native_intake_failure(): native_intake_failure in state keeps the error type, a count of every occurrence and the first and last times, and a native-intake-failed event is appended only for the session's first failure and for a change of error type. When the reconcile failed because the ledger was locked, or when the ledger cannot record it either, that hook run's row in the hook log keeps it under surface_item.native_intake_failure. The inner hook still runs, and pending inputs keep dependent writes closed. list_native_intake_failures() and `--list-native-failures` supply the list that the doctor shows. Consumers compare exact owner, event, digest, count and sequence against SQLite. A copied canonical_input_event_id field in JSON does not create this authority.

Session metadata without canonical provenance can register without another observation only when its event, digest, count and owner match canonical audit evidence. Intake from the host supplies an authoritative sequence boundary. Without one, import sources must match SQLite legacy_migrations hashes; Core Python code reconstructs and compares the exact imported audit prefix, including a pending event, and requires the current observation after that prefix. Those hashes attest import provenance, not current authority for a route or an acceptance criterion.

Registration persists the canonical record atomically. Missing/changed sources without that trusted sequence boundary remain unresolved and cause binding error. The operator restores the required provenance evidence; it must not fabricate input. Import clears copied projection stamps and stales routes.

### Guarded legacy ownership

For admitted ownerless criteria matching an existing current owner's latest frozen execution contract, the response model following the selected procedure reads complete criterion rows and ledger revision. It passes --bind-legacy-criteria-json containing only work_unit_id, contract_revision and exact expected_criteria snapshots, plus --expected-revision and the current route candidate and session metadata.

Core Python code requires canonical intake, exact revision, the admitted same-session execution unit at its latest revision and exact frozen definitions. It compares full row snapshots with type-sensitive values and rejects ambiguous rows, partial ownership or collisions. It adds only the owner pair, preserving order, definition and admission, and writes the ownership audit event.

The CLI binds rows and registers the route in one transaction; either failure rolls back both. Library callers pass the same storage_transaction connection to obtain that boundary. The response model must not resubmit rows through the semantic writer, which creates distinct owned entries. Binding creates no acceptance criteria or admission and does not mark completion.

## Forbidden persistence

For intake events and intent-state fields, callers store only compressed semantic summaries and defined audit metadata. They must not copy raw user prompts, system/developer instructions, tool output, credentials/tokens/API keys/private keys or external fetch bodies into those records. The same SQLite database also contains separate `core_completion_candidates`, `core_completion_assessments` and `core_completion_responses` tables. The completion-assessment API stores the authorized answer draft, authenticated original-source references and review judgments there, as specified by the installed verification-before-completion response-review procedure. Original-source references are not intent summaries; sharing a database does not give the two record types the same content contract.

Current `conduct_feedback` contains open corrections only. Setting a correction to `encoded` removes its prose from current state and records its ID in `retired_feedback_ids`. Semantic-change audit entries retain field names and before/after digests, not additional copies of current or prior values. Authority-bearing constraints, owned acceptance criteria and correction links retain their separate contracts.
