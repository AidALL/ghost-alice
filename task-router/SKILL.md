---
name: task-router
description: "Selects the work, skills, acceptance criteria and boundary contract for a request that needs or may need a tool to change, run, analyze or verify something, or to read a remote source."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

# Task Router

The response model selects the work and the skills from the user's input and from the current `interpreted-as` record when one applies. It reads a stored summary or a referenced detail only when that value can change the selection. Core code validates the submitted response route. Selecting work does not interpret the user's input again.

## Select the next action

The response model interprets the user's input under the common contract's Session Intent section, and that contract's Work Selection section sends work to this skill. For work:

1. Select the focus from the decision that the next action needs, and read the task complexity level as the common contract defines it. When the current session metadata carries `context_selection`, select the information for that decision at the same time: keep the bodies the decision needs, and state the conclusion and the remaining need of each body whose use is finished; a message from the host's own conversation is not exempt from that judgment. Retrieve only referenced constraints, work contracts or criteria that can affect that decision. Do not read all earlier work units.
2. Compare the available skill descriptions with the task. Load the selected skill's body or reuse a loaded one, and load its references only when the next action needs them. A new user input that Core code recorded does not by itself require updating the `interpreted-as` record. One tool call can read several independent required sources; a further model request is justified when the response model must interpret a result before the action that depends on it.
3. Apply boundary-contract for implementation, for work on a mutable or external target, for an unresolved authority boundary, and for verification that changes an acceptance criterion of execution work. A read-only lookup of known sources for an established purpose needs no new boundary contract. Apply using-coding-convention for development, and apply verification-before-completion before a new completion, fix, success, verified-status or material recommendation claim and before rereading an artifact to support a claim. Use systematic-debugging for a failure that needs investigation of its cause and of how deep the correction must go, and to count failed repair rounds.
4. When the answer needs a recorded response review (a completion or partial completion claim, or an explanation review the user explicitly requested) or changes the admitted work selection, register the response route with Core code at that point. Pass `ledger_root`, `platform` and `session_id` from the session metadata, the input ID that `--read-intent` returned when this input's `interpreted-as` record was written or read, the selected work units that this session owns and their acceptance criteria, and the reference to the declared boundary contract. The response route records the current selection; it declares no completion or partial completion claim. When further work requires changing how already-used material appears in the model input, follow [references/context-selection.md](references/context-selection.md) and the `context_selection` procedure in the session metadata.

The response route's `routing_surface` field holds `response_mode`, `boundary_contract` and an optional one-line `reason`. `response_mode` is `normal` for every response route registered through this skill. A direct answer and a confirmation question register no response route.

## Referenced contracts

[references/routing-details.md](references/routing-details.md) defines how to reconcile a new instruction with the recorded boundaries and gives direct-answer examples. [The intent ledger schema](../session-intent-analyzer/references/ledger-schema.md#core-response-projection) defines the `response-route.v1` API and how a response route is bound to the current input. Read that section when registering or changing a response route. Session metadata fields, work unit IDs and the owner of each acceptance criterion come from the host or from stored Core code records; do not infer them from prose.

The response model states the user's objective and the reason for the chosen work. At a handoff, it passes a concise current summary, references to the selected work and the source locators. It does not repeat other skills' procedures.

## Failure modes

Do not infer the authority to execute from stored unfinished work or from paths found in the environment. Do not replace a requested repair with a report. Keep these results distinct: a source change, a check, an installation, the host's actual adoption of the change, the acceptance of a completion claim and work that actually continues. The selected skills define how to correct a failure and when to stop; this skill adds no recovery loop of its own.
