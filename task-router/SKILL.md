---
name: task-router
description: "Use after session-intent intake to decompose requests and route output, verification, lifecycle, and boundary skills without owning intake, raw intent inference, or native tool permission."
calls:
  - "meta:*"
compatibility:
  - "Python 3.11+ standard library"
---

# Task Router

The response model selects work and skills from the supplied request and applicable current intent. It retrieves a stored summary or referenced detail only when that value can change the selection. Core validates the submitted response-route. Routing neither performs native tool permission checks nor repeats intake's semantic analysis.

## Select the next action

The common contract's Session Intent section interprets the input, and its Work Selection section sends work here. For work:

1. Select the response purpose: execution, explanation, approval-wait, partial or completion. A new completion judgment requires its evidence; a progress explanation does not publish completion.
2. Select a focus (micro, meso, macro or meta) and verification depth from the decision actually needed. Select the information for that decision at the same time: retain needed bodies, and express the conclusion and remaining need for bodies whose use is finished. Native origin does not exempt a prior message from that judgment. Retrieve only referenced constraints, work contracts or criteria that can affect that decision. Do not read all historical work units.
3. Match available skill descriptions to the task. Load or reuse the selected skill's procedure, then its referenced detail only when the next action needs it. Intake observation alone does not select the semantic-update procedure. Independent required source reads can share one native tool invocation; a new model round is justified by information that must be interpreted before the dependent action.
4. Apply boundary-contract for implementation, mutable or external targets, an unresolved authority boundary, or verification that changes an execution criterion. A bounded read-only source lookup within an established purpose and known targets requires no new boundary contract. Apply using-coding-convention for development and verification-before-completion for new result claims. Use systematic-debugging for a failure whose cause and sufficient correction depth need investigation, and for repair limits.
5. Before dependent evidence submission, execution-state changes or completion recording, register the Core response-route using the session-start receipt's root, platform and session, the input ID that `--read-intent` returned when this input's interpretation was recorded or read, the selected owned work units and criteria, and the declared boundary reference. When actual continued work requires changing the input representation of already-used material, follow [references/context-selection.md](references/context-selection.md) and the receipt's `context_selection` procedure. Compare that material with the next decision; retain needed details or publish its conclusion, uncertainty and original reference. Combine a changed selection with another required action where possible. A routine lookup does not require a separate route or context-judgment call, and the model does not predict the material's future lifetime. Core authenticates content use independently of work authority; Ghost-ALICE Autopilot's continuation decision does not change this rule. An unpublished selection leaves the body intact. Register an explanation route when the user explicitly requests an authenticated review record.

The routing surface requires only `response_mode` and `boundary_contract`; include decision-relevant optional judgments instead of filling every field. `response_mode` is `normal` for every route registered here, including an explanation route. A direct answer and a confirmation question register no route; `direct-response` and `clarification-only` appear only in a projection the host supplies.

## Referenced contracts

[references/routing-details.md](references/routing-details.md) owns boundary chronology and direct-answer examples. [The ledger schema](../session-intent-analyzer/references/ledger-schema.md#core-response-projection) owns the exact `response-route.v1` API and current-input binding. Read the relevant section when registering or changing that record. Native receipt fields, work-unit IDs and criterion ownership are values supplied by the host or stored Core records, not terms to infer from prose.

The response model names the user objective and the reason for the chosen work. At a handoff, it passes the concise current summary, selected work references and source locators. It does not copy the whole intent ledger or repeat other skills' procedures.

## Failure modes

Do not infer execution authority from stored unfinished work, ambient paths, tools, or routing keywords. Do not replace a requested repair with a report. A route or publication failure does not refute unchanged business proof. Preserve the distinctions between source change, checks, installation, native adoption, completion acceptance and actual continuation. The owning skills determine correction and stopping conditions; routing does not invent another recovery loop.
