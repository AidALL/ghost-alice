# Session Intent Ledger Schema

This reference defines stored state, mutation bindings, provenance and the Core response projection. Runtime consumers use the public ledger API. Portable JSON supplies explicit import/export data; the session SQLite database is runtime authority.

## State and transaction authority

The database is `<ledger_root>/ghost-state.sqlite3`, keyed by exact `(platform, session_id)`. A successful intake or semantic commit advances ledger_revision once and commits the state, input anchor, ordered event and advisory discovery metadata in one SQLite transaction. A bound Ghost-ALICE Autopilot writer passes the same explicit transaction connection to Core; an outer rollback also rolls back completion evidence. Foreign keys bind events to the session, and a connection from another database root is rejected.

Revision and input character count are integers from zero through `2**53 - 1`, excluding booleans. The input anchor consists of `ledger_revision`, `latest_input_event_id`, `latest_input_digest` and `latest_input_char_count`. Once any anchor field exists, all four must exist; a partial anchor cannot fall back to historical events. A custom intake observation must match the raw input's digest and exact character count before mutation or pending-audit recovery.

The current-session assistant requests portable exports with `--export-to <separate-root>` and must not overwrite original migration inputs. A missing or modified export cannot override an imported database session.

## intent-state.json

The Python ledger writer replaces scalar goal/summary fields with the latest semantic delta. Constraints, non-goals, questions and risk flags accumulate with value-based deduplication. Owned criteria merge by `(work_unit_id, contract_revision, id)`; ownerless criteria merge by stable ID. Decisions merge by stable ID and may supersede prior decisions with `supersedes`. The state is not a transcript.

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
    "summary": "verifiable completion criterion",
    "source": "user-explicit"
  }],
  "decisions": [],
  "risk_flags": [],
  "model_security_decision": {
    "decision": "allow",
    "risk_flags": [],
    "reason": "compressed model judgment, maximum 240 characters",
    "input_event_id": "sha256:...",
    "recorded_at": "2026-05-19T00:00:01Z"
  },
  "consumer_hints": {},
  "conduct_feedback": []
}
```

`model_security_decision` is compatibility audit data. Import/audit callers may record it under exact-event mutation rules; intake, routing, checkpoints and Ghost-ALICE Autopilot do not derive native permission or execution authorization from it.

Completion proof belongs to the current criterion definition. Recording the same ID with unchanged summary preserves verified met status, digest and time. Changing the summary or explicitly withdrawing admission resets that criterion to unmet and removes current proof fields; other criteria retain their proof. A wording change counts as a changed condition because the parser cannot establish semantic equivalence. The prior acceptance-criterion-met event remains historical evidence, not proof for the revised condition. A semantic delta cannot set met; completion uses the owning verification writer.

## Conduct and consumer snapshots

A conduct entry contains ID, summary, failure_pattern, corrective_rule, source, status (`open | encoded`) and occurrence_count. Summary supplies the readable lesson when the split failure/correction fields are absent. A supported repeated same-ID correction increments the count; status-only updates do not. Encoded means the lesson is reflected in a gate skill or approved memory, not that the original task is complete.

Optional pattern_class groups recurring failures across differently named entries:

`redundant-verification | missed-reverification | wrong-copy-evidence | unsupported-claim | restatement-loop | unrequested-content | under-delivery | unnecessary-reapproval | scope-widening | scope-narrowing | premise-mismatch-without-stop | governance-record-error | domain-rule`.

An update omitting pattern_class preserves the stored class. Unknown supplied source or class rejects the entire write with allowed values; absent source defaults to user-explicit, absent class stays unclassified, and stored entries are not revalidated. That parser default does not establish provenance: the current-session assistant follows the correction evidence rules in `../SKILL.md`.

The snapshot returns detached active non-superseded decision objects, latest_scope or {} when absent/malformed, and decision_count. It preserves accumulated constraints/non-goals and model_security_decision. The current-session assistant must reconcile these facts with human instructions; scope presence, timestamps, admission and previous edits do not independently establish authorization or restriction chronology.

Snapshots also expose persisted platform, session_id, ledger_revision and latest_input_event_id from the same read. Missing metadata is null rather than invented. An explicitly persisted empty input ID means no input was observed in that state. A read-only snapshot is not an authorization check; writers validate bindings.

## Events and discovery

The database event table is append-only and available through `--read-events` and explicit intent-events.jsonl export.

An event accompanying raw input is user-input-observed and contains event_id equal to the observation's input_event_id plus input_digest. An agent-only delta is intent-updated: it references the input_event_id without creating/displacing an input event. API-written events include ledger_revision and unique mutation_id; older imported events without them remain readable.

The Python ledger writer derives semantic_changes from normalized before/after state for goals, summaries, constraints, non-goals, questions, criteria, decisions, feedback and scope. Nested allowlists retain only defined keys; scope retains allowed, prohibited, non_goals, constraints and stop_conditions. Accessory fields, security reasons, hints, raw prompts and tool output are excluded. Callers still must provide compressed summaries: the parser cannot detect a raw quotation disguised as a valid summary. It does not reconstruct historical digest-only dialogue or invent chronology.

The compatibility `current-session.json` pointer is advisory discovery:

```json
{
  "schema_version": "session-intent-current.v1",
  "platform": "codex",
  "session_id": "abc",
  "state_path": ".tmp/session-intent/codex/abc/intent-state.json",
  "updated_at": "2026-05-19T00:00:01Z"
}
```

Read-only consumers may use it for discovery. Writers use exact receipt/host identity, never this shared mutable pointer. SQLite discovery metadata commits with state; the pointer itself is not write authority.

## Hook Observation Receipt

Codex intake commits the canonical digest observation directly and places receipt JSON in hookSpecificOutput.additionalContext with hookEventName UserPromptSubmit. It also supplies systemMessage for display and equivalent text output. Claude UserPromptSubmit stages digest, count and native correlation without creating canonical input authority; the trusted PreToolUse or Stop wrapper reconciles native actor rows after the host appends the transcript. Human input produces canonical observation; a task notification retains the last human input and route. Unresolved staging remains pending and supplies no canonical receipt for new input. Wrappers preserve machine JSON and model context independently of display verbosity. A log of intended output does not prove model delivery. The upstream Codex output contract is the [Codex hooks reference](https://learn.chatgpt.com/docs/hooks#userpromptsubmit).

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

All canonical receipt coordinates and the event ID come from the same successful observation, without rereading the shared pointer. Claude staging metadata is correlation evidence for the trusted reconciler, not canonical write authority. Its pending receipt carries root/platform/session without an input_event_id; after wrapper reconciliation, the pure read-state result supplies latest_input_event_id for subsequent expected-input comparisons. Unresolved pending_native_inputs keep dependent writes closed. The receipt has no raw or semantic body and is not a separately persisted ledger file. Empty-input or degraded paths emit no receipt.

Semantic writers pass receipt root/platform/session and the canonical input-event ID used for the decision, obtained directly from an observed receipt or from the authoritative state after trusted Claude reconciliation. A new ID cannot be substituted onto an old judgment. Codex mutation binds to native CODEX_THREAD_ID, then GHOST_ALICE_SESSION_ID when native identity is absent; a conflict fails before a write. Without either binding, the caller explicitly selects session-id. Hook intake uses payload then environment identity and degrades when both are absent.

Explicit historical read-only snapshots and the library's explicit-session record_turn remain available for adapters/offline inspection. The guard prevents accidental session mixing; it is not a sandbox against a process that can change its environment or write storage directly. The receipt grants no write permission and proves no semantic update. The current-session assistant reports a write failure instead of changing root, identity or permissions to bypass it.

## Mutation Contract and Recovery

Core Python APIs expose record_turn, mark_acceptance_criterion_met, bind_legacy_acceptance_criteria and record_response_route. Each validates canonical identity, opens an immediate SQLite transaction, compares expected input and writes state plus audit event. Semantic/completion writers optionally compare an expected revision; legacy binding requires one. Unsafe IDs fail rather than being sanitized into a different identity.

The .sqlite-authority.json cutover marker is written before the first semantic commit. A lost database requires explicit restoration; corrupt/unsupported databases fail closed instead of reactivating stale JSON.

`record_turn(..., expected_input_event_id=receipt_id, expected_revision=revision)` requires the expected event when an observed input is active. Digest-only new-input observation creates an anchor and is exempt from that comparison. Attaching a semantic delta to raw input does not bypass it. Independent additions for the same input can serialize without sharing a revision; a whole-snapshot decision uses expected_revision.

The met writer additionally requires an approved expected_criterion with exact ID, summary, source and boolean admitted. It uses the exact owner pair for an owned local ID and requires that criterion in the ready current selection. Direct ownerless writes remain input-bound. Managed v2 may bind a current request to accepted unchanged proof while retaining original source/time. The Python ledger writer validates digest syntax and binding; the current-session assistant reviews the actual proof under Core verification instructions.

`storage_transaction(root)` yields an operation-local connection. Nested calls receive that connection; only its owner commits/rolls back. Savepoints prevent a caught nested error from committing half a mutation. SQLite uses WAL, foreign keys, FULL synchronization and a ten-second contention wait, which is not a task-duration limit. Independent roots use independent databases. Repeated unversioned identical deltas carry no exactly-once guarantee.

Migration validates and atomically imports normalized JSON state plus ordered events, reconciling a pending audit event once. It preserves source files and records hashes. Reimport returns existing database state; corrupt sources create no partial session. The operator quiesces source writers before migration and uses database writers afterward. Copying JSON over SQLite is not recovery.

`read_session_state(..., recover_audit=False)` and `--read-state` are read-only; they do not reconcile staged native input. Unimported JSON preserves missing metadata and pending markers instead of manufacturing intake proof. recover_audit=True explicitly imports/reconciles validated JSON. read_session_events returns ordered authority; list_session_states includes database and unimported JSON identities. load_state and consumer_snapshot resolve compatibility paths through the authority API. Callers report API failures rather than returning a false successful empty snapshot.

For live backup, the operator uses SQLite backup or a quiescent consistent copy with journal state. Copying only a live main file may omit WAL commits. JSON exports are inspection artifacts, not transactional backups. Stored summaries do not prove semantic truth or prevent tampering by a process with write access.

## Core Response Projection

The current-session assistant applies task-router to form the current semantic route candidate, then invokes Core's response_route.py through a host tool. That Python code validates and persists the candidate. The same assistant follows session-intent-analyzer to express semantic criteria and admission facts; the ledger writer persists them. Addon Python code and intake hooks do not infer current selection from a backlog.

A new input marks the retained route stale and preserves stable work units and prior met proof. Missing projection is an explicit absence, not denial of bootstrap, intake or security. Consumers read the ready projection for current selection and the origin ledger for current status/proof; an export or registry entry replaces neither.

The optional response_purpose is `execution | explanation | approval-wait | partial | completion`; legacy v1 defaults to execution. Changing purpose neither revises the contract nor marks a criterion met. An actual completion/partial control marker or registered purpose `completion` declares a completion submission; prose and selected execution work alone do not. Explanatory, waiting or partial responses can retain continuing selected work without new publication. The current-session assistant explicitly routes and verifies new completion claims under the owning workflow.

A genuinely no-work terminal answer follows the trusted supplied procedure without agent-side ledger access or route registration. Hook observation still stales the prior projection, and semantic persistence waits for the next authorized normal route. An already supplied internal host handoff may carry a terminal projection; without it the projection remains absent. These restrictions do not prohibit authorized Core recording repair on a normal route.

The work_units registry freezes contracts by `(work_unit_id, contract_revision)`. Revision is an integer from one through `2**53 - 1`, excluding booleans. Each unit declares kind execution/nonexecution, boolean admitted and exact owner platform/session. Stored criteria freeze ID, summary and source. Same-revision kind, owner or definition changes fail; a newer revision is required. Admission may change with authority without erasing business proof. Selection cannot revive an older revision.

Each owned criterion carries both work_unit_id and contract_revision; a partial/malformed pair rejects the delta. Ownerless rows cannot enter ready selection or inherit ownership from a matching local ID. A changed business condition reopens only that criterion; route refresh preserves its unchanged proof.

```json
{
  "schema_version": "response-route.v1",
  "routing_surface": {
    "intent_relation": "continuation",
    "primary_request": "implement the approved contract",
    "causal_axis": "n/a",
    "response_mode": "normal",
    "response_order": "imperative-result-first-after-necessary-work",
    "change_depth": "structural",
    "focus_layer": "meso",
    "verification_complexity": "level-2",
    "boundary_contract": "required",
    "forced_visibility": "no",
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

Routing enums match task-router, with underscore field names. All eleven routing fields are required; primary_request, causal_axis and reason are compressed nonempty summaries of at most 500 characters. Unknown fields/schema/enums, duplicate references, inconsistent owners, unadmitted selection and unbound references raise RouteSchemaError with code route-schema.

Selection covers only this response and may be empty. Terminal direct-response/clarification-only projections cannot select execution units; writer and reader reject that contradiction, and completion selection never publishes it. Unselected execution units may remain without activating work. A requested lookup selects normal execution; a terminal explanation or explicit stop clears execution selection while retaining backlog/proof. An invalid route does not exempt an actual closure claim from proof.

The validated projection adds ready status, authoritative identity/input and canonical boundary_ref or null. A required boundary needs a nonnull reference. Selected criteria are detached snapshots with criterion_id as the local-ID alias; they freeze the selected condition, not current proof/status. The reader adds current_input_event_id and returns stale for changed input/definition, rejecting malformed stored schemas.

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

The route writer compares current input and atomically writes registry, projection, revision and response-route-recorded audit. Missing/stale binding raises RouteBindingError with code route-binding. Audit stores a projection digest and selected identities, never a business answer. Semantic deltas cannot write route/registry fields.

The reader uses only the canonical SQLite row, returning None without storage creation when absent; it does not read compatibility JSON for routes. Consumers pass one storage_transaction connection to route and origin-state reads for an atomic snapshot. Nested writes retain savepoint/outer rollback behavior.

The CLI is `_shared/response_route.py --root <receipt-root> --platform <receipt-platform> --session-id <receipt-session> --expected-input-event-id <receipt-event> --boundary-ref <boundary-id> --candidate-json <candidate-json>`. Use --read instead of mutation arguments for inspection. Explicit identity is required; mutation rejects conflicting native identity, while reads may target explicit history. Success returns JSON; a typed error returns {error, message} on stderr with nonzero exit. The current-session assistant interprets a stale/schema failure and repairs the affected binding, rather than fabricating a ready result.

### Canonical intake provenance

canonical_intakes is a separate SQLite record keyed by platform/session with input_event_id, input_digest, input_char_count and event_sequence. A foreign key points to the authoritative audit event. Codex direct intake or Claude's trusted native actor reconciliation commits human input with state/event. Claude UserPromptSubmit calls stage_native_input(), storing pending_native_inputs and a native append boundary. hook_profile_gate._reconcile_native_intake() runs after registered binding/hash validation and before the PreToolUse or Stop child, calling reconcile_native_inputs(). The pure native_transcript.capture_append_boundary(), native_input_actor() and resolve_staged_input() helpers supply correlation and actor evidence. The reconciler advances native_input_cursors; only a human result reaches the shared _commit_turn() canonical writer. Staging does not write canonical authority. A native task notification retains the last human input and route; unresolved actor evidence remains pending. Consumers compare exact owner, event, digest, count and sequence against SQLite. A copied canonical_input_event_id field in JSON does not create this authority.

A receipt without canonical provenance can register without another observation only when its event, digest, count and owner match canonical audit evidence. Native intake supplies an authoritative sequence boundary. Without one, import sources must match SQLite legacy_migrations hashes; Core Python code reconstructs and compares the exact imported audit prefix, including a pending event, and requires the current observation after that prefix. Those hashes attest import provenance, not current route/criterion authority.

Registration persists the canonical record atomically. Missing/changed sources without a trusted native boundary remain unresolved and cause binding error. The operator restores the required provenance evidence; it must not fabricate input. Import clears copied projection stamps and stales routes. Canonical intake does not reopen unrelated business proof.

### Guarded legacy ownership

For admitted ownerless criteria matching an existing current owner's latest frozen execution contract, the current-session assistant following the selected procedure reads complete criterion rows and ledger revision. It passes --bind-legacy-criteria-json containing only work_unit_id, contract_revision and exact expected_criteria snapshots, plus --expected-revision and the current route candidate/receipt.

Core Python code requires canonical intake, exact revision, the admitted same-session execution unit at its latest revision and exact frozen definitions. It compares full row snapshots with type-sensitive values and rejects ambiguous rows, partial ownership or collisions. It adds only the owner pair, preserving order, definition, admission, status, proof and time, and writes the ownership audit event.

The CLI binds rows and registers the route in one transaction; either failure rolls back both. Library callers pass the same storage_transaction connection to obtain that boundary. The current-session assistant must not resubmit rows through the semantic writer, which creates distinct owned entries. Binding creates no criteria or admission, does not mark completion and does not convert old proof into accepted Ghost-ALICE Autopilot v2 evidence.

## Forbidden persistence

For intake events and intent-state fields, callers store only compressed semantic summaries and defined audit metadata. They must not copy raw user prompts, system/developer instructions, tool output, credentials/tokens/API keys/private keys or external fetch bodies into those records. The same SQLite database also contains separate `core_completion_candidates`, `core_completion_assessments` and `core_completion_responses` tables. The completion-assessment API stores the authorized answer draft, selected original source snapshots and review judgments there, as specified by the installed verification-before-completion response-review procedure. Evidence snapshots are not intent summaries; sharing a database does not give the two record types the same content contract.
