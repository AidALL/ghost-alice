---
name: task-router
description: "Selects the work, skills, acceptance criteria and boundary contract for a request that needs or may need a tool to change, run, analyze or verify something, or to read a remote source."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

# Task Router

The response model selects work and skills from the supplied request and applicable current intent. It retrieves a stored summary or referenced detail only when that value can change the selection. Core validates the submitted response-route. Routing does not repeat intake's semantic analysis.

## Select the next action

The common contract's Session Intent section interprets the input, and its Work Selection section sends work here. For work:

1. Select the focus from the decision actually needed, and read the task complexity level as the common contract defines it. When the current session receipt carries `context_selection`, select the information for that decision at the same time: retain needed bodies, and express the conclusion and remaining need for bodies whose use is finished; native origin does not exempt a prior message from that judgment. Retrieve only referenced constraints, work contracts or criteria that can affect that decision. Do not read all historical work units.
2. Match available skill descriptions to the task. Load or reuse the selected skill's procedure, then its referenced detail only when the next action needs it. Intake observation alone does not select the semantic-update procedure. Independent required source reads can share one native tool invocation; a new model round is justified by information that must be interpreted before the dependent action.
3. Apply boundary-contract for implementation, mutable or external targets, an unresolved authority boundary, or verification that changes an execution criterion. A bounded read-only source lookup within an established purpose and known targets requires no new boundary contract. Apply using-coding-convention for development, and verification-before-completion before a new completion, fix, success, verified-status or material recommendation claim and before rereading an artifact to support a claim. Use systematic-debugging for a failure whose cause and sufficient correction depth need investigation, and for repair limits.
4. When the answer records a review (a completion or partial claim, or an explicitly requested explanation review) or changes the admitted work selection, register the Core response-route at that point, using the session-start receipt's root, platform and session, the input ID that `--read-intent` returned when this input's interpretation was recorded or read, the selected owned work units and criteria, and the declared boundary reference. The route records the current selection; it declares no completion or partial claim. When actual continued work requires changing the input representation of already-used material, follow [references/context-selection.md](references/context-selection.md) and the receipt's `context_selection` procedure.

The routing surface carries `response_mode`, `boundary_contract` and an optional one-line `reason`. `response_mode` is `normal` for every route registered here. A direct answer and a confirmation question register no route; `direct-response` and `clarification-only` appear only in a projection the host supplies.

## Referenced contracts

[references/routing-details.md](references/routing-details.md) owns boundary chronology and direct-answer examples. [The ledger schema](../session-intent-analyzer/references/ledger-schema.md#core-response-projection) owns the exact `response-route.v1` API and current-input binding. Read the relevant section when registering or changing that record. Native receipt fields, work-unit IDs and criterion ownership are values supplied by the host or stored Core records, not terms to infer from prose.

The response model names the user objective and the reason for the chosen work. At a handoff, it passes the concise current summary, selected work references and source locators. It does not repeat other skills' procedures.

## Failure modes

Do not infer execution authority from stored unfinished work or ambient paths. Do not replace a requested repair with a report. Preserve the distinctions between source change, checks, installation, native adoption, completion acceptance and actual continuation. The owning skills determine correction and stopping conditions; routing does not invent another recovery loop.
