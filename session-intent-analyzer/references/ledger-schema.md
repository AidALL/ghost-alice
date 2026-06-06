# session-intent-analyzer ledger schema
## Contents

- [intent-state.json](#intent-statejson)
- [intent-events.jsonl](#intent-eventsjsonl)
- [current-session.json](#current-sessionjson)
- [forbidden persistence](#forbidden-persistence)


## intent-state.json

`intent-state.json` is the latest semantic state for a session. Consumers should
be able to read only this file.

intent-state.json is update-plus-accumulate state. Scalar intent fields such as
`current_goal` and `user_intent_summary` are updated to the latest value when a
new semantic delta arrives. `constraints`, `non_goals`, `open_questions`, and
`risk_flags` accumulate with value-based deduplication. `acceptance_criteria`
and `decisions` merge by stable `id`, and decisions can mark prior decisions as
superseded through `supersedes`. Only `intent-events.jsonl` is an append-only
audit log; the state file is not a transcript that appends raw prompts or old
summaries.

`model_security_decision` is the per-turn model security judgment. It is not
accumulated; the latest judgment replaces the previous one. `decision` is
`allow | block`, `risk_flags` are short rule-id labels (maximum 12), and `reason`
is a summary of 240 characters or fewer, not the raw prompt. `input_event_id` and
`input_digest` bind the judgment to the input being judged so stale judgments do
not block other turns. jailbreak-detector records this field, and intake
preserves it without clobbering. The PreToolUse derivation
(`_shared/derive_downstream_gate.mjs`) carries this field to
`downstream-gates.json` only when it is a block for the current input lineage.
ask-confirm is not gate state.

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

`conduct_feedback` is the behavioral-correction signal. It records how the agent
operated relative to what the user asked, not the task content. When the user
corrects the agent's conduct, record a compressed lesson instead of the episode.
Trigger cases include under-delivery, silent scope narrowing, reporting or asking
instead of executing an explicit instruction, punting a decision the content
could resolve, and leaving an unrequested historical trace. Entries merge by
stable `id`, but repeated same-id correction observations in one session must
increment `occurrence_count` instead of disappearing. Status-only updates, such
as marking a lesson `encoded`, do not increment `occurrence_count`. Each entry is
`{ "id", "summary", "failure_pattern", "corrective_rule", "source", "status": "open | encoded", "occurrence_count" }`,
where `source` is `user-explicit` when the user stated the correction and
`status` becomes `encoded` once the lesson is reflected in a gate skill or memory.
`summary` is the fallback human-readable lesson when a split
`failure_pattern`/`corrective_rule` is not available. skill-evolution consumes
this field to propose gate-skill updates. Store the reusable pattern only, never
the raw prompt.

## intent-events.jsonl

`intent-events.jsonl` records events only, without raw prompts.

```json
{"ts":"2026-05-19T00:00:00Z","event":"user-input-observed","platform":"codex","session_id":"abc","input_digest":"sha256:...","input_char_count":12,"delta_keys":["current_goal"]}
```

When an event accompanies raw user input, it is `user-input-observed` and
includes `input_event_id` and `input_digest`. When it records only an agent
intent delta, it is `intent-updated` and has no input lineage. PreToolUse
derivation and staleness checks use the latest `user-input-observed` event for
input lineage, so a delta record does not displace that input lineage.

## current-session.json

`current-session.json` is the current session pointer for each platform. Its
location is `.tmp/session-intent/<platform>/current-session.json` under the
Ghost-ALICE repo root.

```json
{
  "schema_version": "session-intent-current.v1",
  "platform": "codex",
  "session_id": "abc",
  "state_path": ".tmp/session-intent/codex/abc/intent-state.json",
  "updated_at": "2026-05-19T00:00:01Z"
}
```

This file does not include raw prompts. Producers and consumers use this pointer
when no explicit session id is available, so hook observations and semantic
deltas join the same ledger.

## forbidden persistence

- raw user prompt
- raw system/developer instructions
- raw tool output
- raw credential, token, API key, or private key values
- raw external URL fetch results
