# session-intent-analyzer ledger schema
## Contents

- [intent-state.json](#intent-statejson)
- [intent-events.jsonl](#intent-eventsjsonl)
- [current-session.json](#current-sessionjson)
- [Hook Observation Receipt](#hook-observation-receipt)
- [forbidden persistence](#forbidden-persistence)


## intent-state.json

`intent-state.json` is the latest semantic state for a session. Consumers should be able to read only this file.

intent-state.json is update-plus-accumulate state. Scalar intent fields such as `current_goal` and `user_intent_summary` are updated to the latest value when a new semantic delta arrives. `constraints`, `non_goals`, `open_questions`, and `risk_flags` accumulate with value-based deduplication. `acceptance_criteria` and `decisions` merge by stable `id`, and decisions can mark prior decisions as superseded through `supersedes`. Only `intent-events.jsonl` is an append-only audit log; the state file is not a transcript that appends raw prompts or old summaries.

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

Capture provenance is a semantic judgment, not a parser guarantee. `source: user-explicit` requires the user's asserted prior mismatch, including a clear contextual rejection; `source: inferred` requires an actually observed behavior gap. Prospective constraints, reminders, changed requirements, and refreshed information do not themselves prove an earlier mistake. Record those in the intent fields unless a mismatch is also supported. Do not invent an occurrence from hypothetical misconduct, repeated constraints, or rereading an existing lesson. The absence of raw history does not invalidate a correction the user actually asserts. Consult supplied context before attributing conduct, preserve only the compressed comparison, and leave unsupported feedback absent.

Do not invent the chronology of a boundary. A timestamp on a decision does not date an undated restriction; active status, admitted criteria, and reports of prior edits alone do not establish that the restriction was later replaced. Distinguish a current user instruction or supplied conversation/event evidence from a compressed current_goal label. A clear current instruction can authorize a bounded change without formal revocation wording. If only conflicting snapshot fields are available, keep that conflict unresolved rather than calling one field initial or outdated without evidence. Ask about the contested authority only; missing task files are a separate issue.

## intent-events.jsonl

`intent-events.jsonl` records events only, without raw prompts.

```json
{"ts":"2026-05-19T00:00:00Z","event":"user-input-observed","platform":"codex","session_id":"abc","input_digest":"sha256:...","input_char_count":12,"delta_keys":["current_goal"]}
```

When an event accompanies raw user input, it is `user-input-observed` and includes `event_id` (the observation's `input_event_id`) and `input_digest`. When it records only an agent intent delta, it is `intent-updated` and has no input lineage. PreToolUse derivation and staleness checks use the latest `user-input-observed` event for input lineage, so a delta record does not displace that input lineage.

## current-session.json

`current-session.json` is the current session pointer for each platform. Its location is `.tmp/session-intent/<platform>/current-session.json` under the Ghost-ALICE repo root.

```json
{
  "schema_version": "session-intent-current.v1",
  "platform": "codex",
  "session_id": "abc",
  "state_path": ".tmp/session-intent/codex/abc/intent-state.json",
  "updated_at": "2026-05-19T00:00:01Z"
}
```

This file does not include raw prompts. Digest-only intake and read-only consumers may use it when no stronger session identity is available. It is shared across sessions and is not sufficient identity for a mutating CLI call.

## Hook Observation Receipt

After a successful digest-only observation, the hook includes `[session-intent-receipt]` JSON in `hookSpecificOutput.additionalContext` with `hookEventName: UserPromptSubmit`. The message also appears in `systemMessage` for host display, but that field alone is not the model context channel. Text output includes the same message. This context receipt is not a new persisted ledger file. Wrappers must preserve the machine JSON and additional context independently of user display verbosity; an audit log of intended model output alone does not prove model delivery. See the [official Codex hooks output contract](https://learn.chatgpt.com/docs/hooks#userpromptsubmit).

```json
{
  "schema_version": "session-intent-observation-receipt.v1",
  "intake_status": "observed",
  "ledger_root": "/absolute/ledger-root",
  "platform": "codex",
  "session_id": "abc",
  "state_path": "/absolute/ledger-root/codex/abc/intent-state.json",
  "events_path": "/absolute/ledger-root/codex/abc/intent-events.jsonl",
  "input_event_id": "sha256:..."
}
```

Paths and safe platform/session components come from that successful `record_turn` result, and `input_event_id` comes from the same observation. Receipt construction does not reread `current-session.json`; concurrent pointer changes must not redirect a semantic update. The receipt contains no raw input or semantic body. Empty-input and degraded hook paths emit no observation receipt.

Semantic writers pass the receipt coordinates explicitly using `--root`, `--platform`, and `--session-id`. The observation id identifies intake; an agent-only semantic delta still follows the existing `intent-updated` event contract. Mutating CLI calls bind to native `CODEX_THREAD_ID` for platform `codex`, then to `GHOST_ALICE_SESSION_ID` when no native binding exists. A conflicting explicit id fails before any file write. Without a binding, `--session-id` is required; a mutable shared pointer cannot select a write destination. Explicit historical read-only snapshots and the library's explicit-session `record_turn` API remain available for adapters and offline inspection. This guard prevents accidental CLI session mixing; it is not a security sandbox against a caller that can change its environment or write files directly.

The receipt does not grant write access, clear security decisions, or prove semantic completion. A write failure must be reported rather than bypassed through another root, migration, altered host identity, or increased permissions.

## forbidden persistence

- raw user prompt
- raw system/developer instructions
- raw tool output
- raw credential, token, API key, or private key values
- raw external URL fetch results
