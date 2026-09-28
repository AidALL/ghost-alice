# session-intent-analyzer ledger schema
## Contents

- [intent-state.json](#intent-statejson)
- [intent-events.jsonl](#intent-eventsjsonl)
- [current-session.json](#current-sessionjson)
- [Hook Observation Receipt](#hook-observation-receipt)
- [Mutation Contract and Recovery](#mutation-contract-and-recovery)
- [forbidden persistence](#forbidden-persistence)


## intent-state.json

`intent-state.json` describes the portable state shape. The authoritative store is `<ledger_root>/ghost-state.sqlite3`, keyed by `(platform, session_id)`. Runtime consumers call `read_session_state` or CLI `--read-state` with exact receipt coordinates. A missing or modified JSON export does not override an imported database session.

`ledger_revision` advances once per successful semantic/intake commit. State, its current input anchor, its ordered audit event and advisory discovery metadata commit in one SQLite transaction. Bound Autopilot writers pass the same explicit `transaction` connection to Core, so an outer rollback also rolls back completion evidence. Foreign keys bind events to their session; a supplied connection from another database root is rejected. JSON exports are requested explicitly with `--export-to <separate-root>` and must not overwrite original migration inputs.

Revision and input character count are integers from `0` through `2**53 - 1`, excluding booleans, so Python and JavaScript readers agree. All four anchor fields must be present together once any is present; partial new anchors are invalid and cannot fall back to historical events. A custom intake observation must match the raw input's digest and exact integer character count before any mutation or pending-audit recovery occurs.

Completion evidence belongs to the criterion's current definition. Re-recording the same ID and unchanged summary preserves verified `met` status, digest, and timestamp. A changed summary or explicit withdrawal of admission resets that criterion to `unmet` and removes its current proof fields; unrelated criteria retain their evidence. A wording change is conservatively treated as a changed condition because the parser cannot certify semantic equivalence. The existing `acceptance-criterion-met` event remains historical evidence, not proof for the revised condition. A raw delta cannot promote a condition to `met`; revalidation uses the normal completion-check path.

intent-state.json is update-plus-accumulate state. Scalar intent fields such as `current_goal` and `user_intent_summary` are updated to the latest value when a new semantic delta arrives. `constraints`, `non_goals`, `open_questions`, and `risk_flags` accumulate with value-based deduplication. `acceptance_criteria` and `decisions` merge by stable `id`, and decisions can mark prior decisions as superseded through `supersedes`. The database event table is an append-only audit log exposed through `--read-events` and explicit JSONL exports; the state file is not a transcript that appends raw prompts or old summaries.

`model_security_decision` is the per-turn model security judgment. It is not accumulated; the latest judgment replaces the previous one. `decision` is `allow | block`, `risk_flags` are short rule-id labels (maximum 12), and `reason` is a summary of 240 characters or fewer, not the raw prompt. `input_event_id` and `input_digest` bind the judgment to the input being judged so stale judgments do not block other turns. jailbreak-detector records this field, and intake preserves it without clobbering. The PreToolUse derivation (`_shared/derive_downstream_gate.mjs`) carries this field to `downstream-gates.json` only when it is a block for the current input lineage. ask-confirm is not gate state.

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

`conduct_feedback` is the behavioral-correction signal. It records how the agent operated relative to what the user asked, not the task content. When the user corrects the agent's conduct, record a compressed lesson instead of the episode. Trigger cases include under-delivery, silent scope narrowing, reporting or asking instead of executing an explicit instruction, punting a decision the content could resolve, and leaving an unrequested historical trace. Entries merge by stable `id`, but repeated same-id correction observations in one session must increment `occurrence_count` instead of disappearing. Status-only updates, such as marking a lesson `encoded`, do not increment `occurrence_count`. Each entry is `{ "id", "summary", "failure_pattern", "corrective_rule", "source", "status": "open | encoded", "occurrence_count" }`, where `source` is `user-explicit` when the user stated the correction and `status` becomes `encoded` once the lesson is reflected in a gate skill or memory. `summary` is the fallback human-readable lesson when a split `failure_pattern`/`corrective_rule` is not available. skill-evolution consumes this field to propose gate-skill updates. Store the reusable pattern only, never the raw prompt.

The consumer snapshot includes `active_decisions`, a detached copy of the non-superseded decision objects, and `latest_scope`, a detached copy of the recorded scope object or `{}` when absent or malformed. `decision_count` remains the number of active decisions. These are additive semantic-context fields, not permission or completion evidence. The snapshot preserves accumulated constraints and non-goals and leaves `model_security_decision` unchanged. Recorded scope does not automatically supersede list entries or relax a downstream gate.

Snapshots also expose the persisted `platform`, `session_id`, `ledger_revision`, and `latest_input_event_id` from the same authoritative state read. Missing legacy metadata is `null`, not an invented revision or input anchor. An explicitly persisted empty input ID means no input has been observed in that state. A permissive historical snapshot is still not an authorization check; mutation APIs perform strict validation.

Capture provenance is a semantic judgment, not a parser guarantee. `source: user-explicit` requires the user's asserted prior mismatch, including a clear contextual rejection; `source: inferred` requires an actually observed behavior gap. Prospective constraints, reminders, changed requirements, and refreshed information do not themselves prove an earlier mistake. Record those in the intent fields unless a mismatch is also supported. Do not invent an occurrence from hypothetical misconduct, repeated constraints, or rereading an existing lesson. The absence of raw history does not invalidate a correction the user actually asserts. Consult supplied context before attributing conduct, preserve only the compressed comparison, and leave unsupported feedback absent.

Do not invent the chronology of a boundary. A timestamp on a decision does not date an undated restriction; active status, admitted criteria, and reports of prior edits alone do not establish that the restriction was later replaced. Distinguish a current user instruction or supplied conversation/event evidence from a compressed current_goal label. A clear current instruction can authorize a bounded change without formal revocation wording. If only conflicting snapshot fields are available, keep that conflict unresolved rather than calling one field initial or outdated without evidence. Ask about the contested authority only; missing task files are a separate issue.

## intent-events.jsonl

`intent-events.jsonl` is a portable event export. Runtime readers use `--read-events`; events record no raw prompts.

```json
{"ts":"2026-05-19T00:00:00Z","event":"user-input-observed","platform":"codex","session_id":"abc","input_digest":"sha256:...","input_char_count":12,"delta_keys":["current_goal"]}
```

When an event accompanies raw user input, it is `user-input-observed` and includes `event_id` (the observation's `input_event_id`) and `input_digest`. An agent-only delta is `intent-updated`: its `input_event_id` references the applicable observation but does not create or displace an input event. Each new event also includes `ledger_revision` and a unique `mutation_id`. Older events remain readable without these fields.

`semantic_changes` records before/after projections of changed goals, intent summaries, constraints, non-goals, open questions, acceptance criteria, decisions, conduct feedback, and scope. It is derived from normalized state rather than the raw delta. Nested entries use explicit key allowlists; scope preserves `allowed`, `prohibited`, `non_goals`, `constraints`, and `stop_conditions`. Unknown accessory fields, security reasons, consumer hints, raw prompts, and tool-output fields are excluded. This makes intermediate scalar corrections and criterion reopening inspectable without copying arbitrary request objects. Callers must still supply compressed semantic summaries: the code does not detect raw quotations disguised inside a valid summary field. Historical digest-only events are not reconstructed or assigned invented chronology.

## current-session.json

`current-session.json` is a legacy advisory pointer for each platform; new writes update SQLite discovery metadata atomically. Its location is `.tmp/session-intent/<platform>/current-session.json` under the Ghost-ALICE repo root.

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

All supported state mutations use `record_turn` or `mark_acceptance_criterion_met`. Both validate exact canonical platform/session identity, open an immediate SQLite transaction, compare expected input/revision and merge state with its audit event. Unsafe identifiers are rejected rather than sanitized into another identity. The root `.sqlite-authority.json` marker records cutover before the first semantic commit, so a lost database requires explicit restoration instead of recreating state from stale JSON. Corrupt databases and unsupported schema versions fail closed; they never reactivate stale JSON exports.

`record_turn(..., expected_input_event_id=receipt_id, expected_revision=revision)` requires the expected input ID whenever an observed input is active. A digest-only new-input intake creates an anchor and is exempt from that comparison; attaching a semantic delta to new raw input does not bypass the check. `expected_revision` additionally fences decisions made against a whole-state snapshot. Independent additions for the same input can serialize without requiring a common revision. Security decisions must name that input in their own payload.

`mark_acceptance_criterion_met` additionally requires the approved `expected_criterion` with exact `id`, `summary`, `source`, and boolean `admitted`. Changed conditions or earlier inputs cannot reuse proof. The writer validates digest syntax and binding; the caller must validate the actual completion evidence. A raw delta cannot assert completion.

`storage_transaction(root)` yields a connection; pass `transaction=connection` to nested Core calls. Only its owner commits or rolls back. Nested mutations use savepoints, so catching a failed nested operation cannot commit half that mutation. Connections are operation-local. SQLite uses WAL, foreign keys, FULL synchronization and a ten-second contention wait; this wait is not a limit on task duration. Independent roots use independent databases. No exactly-once claim is made for an unversioned caller deliberately submitting the same delta twice.

`migrate_session` and first mutation validate a legacy session and import its normalized state plus ordered events atomically. A pending legacy audit event is reconciled once in the import transaction. Source files remain unchanged and their hashes are recorded. Repeated import returns the existing database state; a corrupt legacy record creates no partial session. Stop legacy writers before cutover. Do not run old and new writers against one root after import. Retain the originals for inspection; never roll back by copying stale JSON over current authority.

`read_session_state(..., recover_audit=False)` is read-only. Unimported legacy state preserves absent metadata rather than fabricating proof of intake, including a pending audit marker if present. A committed current security block remains visible even while its old audit projection awaits import. `recover_audit=True` explicitly imports/reconciles legacy state. `read_session_events` returns ordered authority; `list_session_states` includes database sessions and unimported legacy identities. API failures must be reported, not converted into a successful empty snapshot. `load_state` and `consumer_snapshot` resolve canonical compatibility paths through the same database authority.

For live database backups use SQLite's backup API or a quiescent, consistent database copy including its journal state; copying only an active main database file can omit committed WAL data. Explicit JSON exports are portable inspection artifacts, not transactional backup or runtime authority. Stored summaries do not establish semantic truth and these guards do not protect against arbitrary direct database tampering by a process with write access.

## forbidden persistence

- raw user prompt
- raw system/developer instructions
- raw tool output
- raw credential, token, API key, or private key values
- raw external URL fetch results
