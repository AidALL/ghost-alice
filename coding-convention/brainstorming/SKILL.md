---
name: brainstorming
description: "Resolves open design decisions, including expected behavior, asking the human user for each decision only the human user can make, and writes the design the human user accepted before implementation."
calls:
  - "hard:writing-plans"
compatibility:
  - "Python 3.11+ standard library"
---

# Turning Ideas into Designs

The response model asks the human user for each unsettled design decision only the human user can make and settles technical choices within what the human user's instructions authorize before implementation. Reuse the requirements and design that the human user accepted and the authority that the human user gave.

## Design procedure

1. Inspect the relevant project structure, documentation and recent changes when the human user's instructions authorize that inspection. Identify the objective, constraints, acceptance criteria and decisions that the human user's instructions already supplied (for a delegate, as the assignment from the parent, the session or agent that started the delegate, states them), and the constraints that the project instructions add.
2. Decompose a request containing independent subsystems into independently understandable specifications. Preserve the complete objective and the relationships and execution order between subsystems.
3. Ask one decisive question at a time for missing human-owned decisions. Prefer concise choices when useful. A user example supplies evidence about desired behavior; resolve the acceptance criterion it illustrates rather than making the example the whole contract.
4. For a live design choice, compare two or three alternatives, explain the trade-offs and recommend one. Without contrary evidence, do not reopen a choice the human user settled or a technical choice the response model settled within what the human user's instructions authorize.
5. Present a concrete design covering components, interfaces, data flow, error handling and verification. Use enough detail to resolve the uncertainty.
6. Save the design that the human user accepted to `docs/coding-convention/specs/YYYY-MM-DD-<topic>-design.md`, unless the user specified another location or prohibited an artifact. Follow the commit policy in the human user's instructions.
7. Review the specification for missing requirements, contradictions, ambiguous behavior, unnecessary features and boundaries too broad for one plan. Correct in-scope defects, retaining decisions that the human user already accepted.
8. Invoke writing-plans to turn the design that the human user accepted into implementation tasks. Brainstorming itself does not invoke an implementation skill.

## Design boundaries

Each component needs a named responsibility, interface and dependency set. In an existing project, follow its established patterns. Include structural improvement when the observed defect affects the work that the human user requested; do not add unrelated refactoring.

Contrary evidence can require revisiting a requirement, component or data-flow assumption. Identify the affected decision, revise it within existing authority and verify that correction. Follow boundary-contract for any change requiring new human authority.

Use [references/document-reviewer-prompt.md](references/document-reviewer-prompt.md) when an independent specification review can resolve an uncertainty the self-review leaves. Do not dispatch a delegate merely to repeat the review delegate's unchanged `Approved` result.

## Visual companion

Offer the browser companion once when a forthcoming design decision benefits from mockups, layouts or diagrams. Present the offer alone, state that it opens a local URL and may consume substantial tokens, and wait for the user's choice. A decline keeps this companion disabled; use text for the design discussion.

If the user accepts, read [references/visual-companion.md](references/visual-companion.md) before starting it. Decide per question whether a visual helps: layout comparisons fit the browser, while requirements and textual trade-offs normally fit the conversation. Consent to the companion does not require every question to use it.

## Failure modes and output

Do not treat a simple task as exemption from a real unresolved design decision. Do not silently narrow the objective.

The output is the specification that the human user accepted and any exact unresolved decision, followed by writing-plans when its prerequisites are available. Verification-before-completion governs claims that the specification is complete.
