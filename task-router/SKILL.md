---
name: task-router
description: "Use after session-intent intake to decompose requests and route output, verification, lifecycle, and boundary skills without owning intake, raw intent inference, or native tool permission."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

# Task Router

The current-session assistant applies task-router after session-intent intake to perform request decomposition, work placement, skill routing and boundary-skill selection. The current-session assistant uses accepted intent without repeating raw user intent inference, granting authority, obtaining credentials or deciding native tool permission. Core's response_route.py validates and stores the candidate that this assistant submits.

A delegated assistant assigned only a specific subtask skips this routing workflow. The parent assistant retains responsibility for routing the user's request.

## Entry and authority

The common governance First-Entry Contract owns classification before tools, pending-merge intake order, terminal answers and opening visibility. After that contract selects normal work, the current-session assistant applies this body to the exact session-intent state. The same session may supply skill-evolution's independent report-only branch.

Missing intake context leaves dependent registration unavailable. Restore only the context needed by an authorized next action; do not substitute another session. Apply boundary-contract for authority, resource limits and renewal.

## Select the response and work

1. Use the pending-merge result obtained under the common First-Entry Contract; apply merge-companion only for its reported undecided entries.
2. Consume the exact session-intent summary. Reconcile current goal, latest scope, active decisions, accumulated constraints/non-goals and acceptance criteria. Apply boundary chronology from `references/routing-details.md` when those facts conflict. Ask only for an unresolved human-owned decision; existing authorization does not need renewed approval.
3. Identify the primary question or instruction, action category, domain, output shape, input file type, verification signal, boundary signal and sufficient change depth. Preserve a causal axis only when the request asks about a cause or relationship.
4. Reconcile unfinished authorized work before selecting a terminal response. A status question or correction normally receives a brief commentary answer followed by continued work. An explicit stop, pause, cancellation or incompatible replacement governs continuation. An ambient workspace, tool availability or an unresolved ledger flag alone grants no authority.
5. Select the response mode using the table below. Route classification precedes evidence planning. A general explanatory premise does not become an inspection request because verification could be performed.
6. Match available skills, establish a required boundary, and register the normal route within the user's tool budget before the downstream consumer captures its response.

| Mode | Condition | Agent action |
| --- | --- | --- |
| `clarification-only` | An essential referent or decision is missing, and the conversation supports neither an answer nor safe work | Ask the minimum decisive question; perform no agent-side inspection or metadata repair |
| `direct-response` | Supplied conversation or retained valid evidence supports a bounded answer without work, lookup, tools or fresh verification | Return the resolved content; perform no agent-side inspection or metadata repair |
| `normal` | Authorized work continues, or the request needs lookup, inspection, modification, verification, current/version-specific claims or high-risk advice | Follow the owning work and verification workflows |

The terminal restrictions govern genuinely no-work answers. They do not prohibit authorized Core route repair on a normal completion path. A delivery instruction about visible answer text does not change the route or remove a required recording operation. The full terminal conditions and examples are in `references/routing-details.md`.

The current-session assistant preserves the attained stage when reporting progress: feedback recorded, source changed, behavior tested, and runtime installed/adopted are distinct claims. A branch-local obstacle blocks only dependent work. When the user authorizes correction of an execution failure, the current-session assistant routes implementation and an original-failure regression; a conduct-feedback record or evolution report does not implement the correction.

## Match skills and scope

The current-session assistant applying task-router scans available descriptions against the accepted action, domain, input type and verification burden. It records a plausible match or a short reason for no match.

- Output skills produce the requested artifact. No output match does not block work.
- Adversarial-verification handles admitted source-heavy, consistency, factual, numeric, legal, patent, grant and investor-relations claims.
- Necessity-gate runs when the current-session assistant defines new work, a file, an audit cycle or a follow-up.
- Using-coding-convention runs at the start of development work.
- Verification-before-completion runs before an executed-work closure or freshly verified result.
- Other lifecycle skills run when their applicable phase is reached.

The current-session assistant applying task-router sets `boundary-contract: required` for implementation, modification or verification; explicit prohibited surfaces; auth/API/DI/navigation/dependency/config/schema or external side effects; discovery of unknown target files/tests; constrained visual/read-only checks; or a correction to objective/scope/priority/completion meaning when actionable work remains. The current-session assistant applying task-router states the reason only. The same assistant applies boundary-contract to state filenames, allowed surfaces, test purpose and stop conditions.

When required, boundary-contract runs before discovery, output work and edits, then using-coding-convention runs for development. Without a required boundary, using-coding-convention runs immediately for development.

The current-session assistant selects the focus needed for the next decision, independently of verification depth. A meta judgment can revise the assistant's premise inside existing authority; it does not itself require a user question. The assistant selects `minimal | localized | structural | systemic` change depth from the supported cause and impact surface. A structural cause requires a sufficient structural correction. A full-set objective cannot become a sample. A user-authorized temporary correction retains its residual impact.

## Routing records

The routing-surface is the reusable work judgment for boundary, verification, lifecycle and visibility consumers. The current-session assistant records semantic facts through the intake writer, then makes this work judgment while applying task-router.

```text
[routing-surface]
- intent-relation: new | continuation | accepted-continuation | changed | correction | ambiguous
- primary-request: <primary question or instruction>
- causal-axis: <cause or relationship | n/a>
- response-mode: normal | clarification-only | direct-response
- response-order: causal-answer-first-after-necessary-evidence | imperative-result-first-after-necessary-work | clarification-question-only | resolved-intent-first
- change-depth: minimal | localized | structural | systemic
- focus-layer: micro | meso | macro | meta
- verification-complexity: level-1 | level-2 | level-3
- boundary-contract: required | n/a
- forced-visibility: yes | no
- reason: <decision-relevant reason>
```

`accepted-continuation` requires recorded acceptance in intent facts, not a phrase alone. Unknown routing-surface values fail closed: consumers expose the full surface and reopen focus. Focus and complexity use the session gate matrix's existing levels. The routing record grants no tool permission.

For normal causal questions, the final answer leads with the supported causal answer after necessary evidence; for imperative requests, it leads with the result after necessary work. The response-order never requires inventing an answer before work. Terminal responses use clarification-question-only or resolved-intent-first respectively.

```text
[task-router]
domain: <domain>
response-mode: normal | clarification-only | direct-response
output-skills: <skill and reason | none>
verification-skills: <skill and reason | none>
lifecycle: <applicable skills | none>
boundary-contract: required | n/a
boundary-reason: <reason when required>
next-required: boundary-contract | <skill> | user-input | none
```

A normal route reports factual gate state using the common contract's `[gate-state]` schema. Runtime hooks own tool-checkpoint visibility and strict audit.

Instruction-body reuse follows `../coding-convention/verification-before-completion/references/verify-or-reuse.md`. On Codex, a skill-call record requires a fresh or valid retained body and execution of its current workflow. Runtime hooks own tool-checkpoint schema, per-input visibility deduplication and strict audit. Its stable meaning is `hook-stage: PreToolUse` and `meaning: tool-call retry checkpoint, not user-input intake`.

## Register the Core projection

For a normal route, the current-session assistant submits `response-route.v1` through `_shared/response_route.py` or `record_response_route` using exact current input binding: receipt root, platform, session ID and expected input-event ID. When a boundary is required, the current-session assistant declares it first and supplies its reference. The complete schema, guarded legacy binding and API behavior are in `../session-intent-analyzer/references/ledger-schema.md`, section Core Response Projection.

The current-session assistant following the selected procedure records each criterion with its work-unit ID and contract revision before selection. The current-session assistant applying task-router assigns stable IDs to continuing authorized work and selects only currently authorized, admitted units and criteria. A repeated local criterion ID does not establish ownership. Changed kind, owner or criterion definition requires a newer contract revision.

The current-session assistant records response purpose independently as `execution | explanation | approval-wait | partial | completion`. Core selects completion processing from an actual completion/partial control marker or registered purpose `completion`; prose wording and an execution work selection do not declare completion or publication. The current-session assistant identifies its new completion claims, explicitly routes them and applies verification-before-completion. Explanation or progress can retain continuing work without submitting completion. Legacy candidates without purpose retain execution behavior.

For exact existing ownerless criteria, the current-session assistant calls Core's guarded legacy-binding API described in the schema reference through a host tool. It does not resubmit rows through the semantic writer or invent ownership, admission or evidence. A changed input requires reconciliation and a current route binding; it does not erase stable work units or unchanged business proof.

A terminal answer may use only an already supplied internal host handoff. If none exists, the projection stays absent. The current-session assistant must not open tools or manufacture a receipt merely to register that answer. A stale/schema rejection leaves dependent registration unavailable. The assistant corrects the affected binding or contract when a required claim or authorized next action needs it; it never attaches an old judgment to a new input event.

## Failure modes and repair

The current-session assistant must not infer inspection authority from a general question, replace a requested implementation with reporting, or use a route/purpose label to evade verification of a real completion claim. A formatting or route-recording error does not invalidate unchanged business proof.

For authority and contract renewal, apply `../boundary-contract/SKILL.md#authority-and-renewal`. For incident counts and continuation, apply `../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation`. For useful checks, apply `../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate`; that skill and its references also own proof reuse, Ghost-ALICE Autopilot publication and native delivery. Preserve original evidence source and time; run affected new verification only for a changed condition, contrary evidence, explicit recheck or an actually expanded claim.
