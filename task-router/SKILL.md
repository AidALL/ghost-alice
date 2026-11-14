---
name: task-router
description: "Selects the work, skills, acceptance criteria and boundary contract for a request that needs or may need a tool to change, run, analyze or verify something, or to read a remote source."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

# Task Router

The response model selects the work and the skills for the input the response model is answering, within the objective, constraints and acceptance criteria that the current `interpreted-as` record holds, when one applies. It reads a stored summary or a referenced detail only when that value can change the selection. Core code validates the submitted response route. Selecting work does not reinterpret the input the response model is answering.

## Select the next action

The response model interprets the input the response model is answering under the common contract's Session Intent section, and that contract's Work Selection section sends work to this skill. For work:

1. Select the focus from the decision that the next action needs, and read the task complexity level as the common contract defines it. When the current session metadata carries `context_selection` or the decision needs an original source again, follow [references/context-selection.md](references/context-selection.md) to select the information for that decision. Retrieve only stored constraints, work contracts or criteria that can affect the decision.
2. Compare the available skill descriptions with the task. Load the selected skill's body or reuse a loaded one, and load its references only when the next action needs them.
3. Apply boundary-contract for implementation, for work on a mutable or external target, for an unresolved authority boundary, and for verification that changes an acceptance criterion of execution work. A read-only lookup of known sources for the human user's established objective needs no new boundary contract. Apply using-coding-convention for development, and apply verification-before-completion before a new completion, fix, success, verified-status or material recommendation claim and before rereading an artifact to support a claim. Use systematic-debugging for a failure that needs investigation of its cause and of how deep the correction must go, and to count failed repair rounds.
4. When the answer needs a recorded response review (a completion or partial completion claim, or an explanation review the human user explicitly requested) or changes the admitted work selection, register the response route with Core code at that point. Pass `ledger_root`, `platform` and `session_id` from the session metadata, the input ID that `--read-intent` returned when this input's `interpreted-as` record was written or read, the selected work units that this session owns and their acceptance criteria, and the reference to the declared boundary contract. The response route records the current selection; it declares no completion or partial completion claim.

## Referenced contracts

[references/routing-details.md](references/routing-details.md) defines how to reconcile a new instruction from the human user with the recorded boundaries and gives direct-answer examples. [The intent ledger schema](../session-intent-analyzer/references/ledger-schema.md#core-response-projection) defines the `response-route.v2` API and how a response route is bound to the current input that Core code recorded. Read that section when registering or changing a response route. Session metadata fields, work unit IDs and the owner of each acceptance criterion come from the host or from stored Core code records; do not infer them from prose.

## Failure modes

Do not infer the authority to execute from stored unfinished work or from paths found in the environment. Do not replace a repair that the human user requested with a report. Keep these results distinct: a source change, a check, an installation, the host's actual adoption of the change, a completion claim passing Core code's Stop script check against the recorded response review and work that actually continues. The selected skills define how to correct a failure and when to stop; this skill adds no recovery loop of its own.
