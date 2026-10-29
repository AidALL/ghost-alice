---
name: brainstorming
description: "Resolves open design decisions, including expected behavior, with the user and writes the accepted design before implementation."
calls:
  - "hard:writing-plans"
compatibility:
  - "Python 3.11+ standard library"
---

# Turning Ideas into Designs

The agent resolves unsettled product or technical design decisions before implementation. Reuse accepted requirements, design and authority.

## Design procedure

1. Inspect the relevant project structure, documentation and recent changes when the task authorizes that inspection. Identify the objective, constraints, success criteria and decisions already supplied.
2. Decompose a request containing independent subsystems into independently understandable specifications. Preserve the complete objective and the relationships and execution order between subsystems.
3. Ask one decisive question at a time for missing human-owned decisions. Prefer concise choices when useful. A user example supplies evidence about desired behavior; resolve the criterion it illustrates rather than making the example the whole contract.
4. For a live design choice, compare two or three alternatives, explain the trade-offs and recommend one. Do not reopen settled choices without contrary evidence.
5. Present a concrete design covering components, interfaces, data flow, error handling and verification. Use enough detail to resolve the uncertainty.
6. Save the accepted design to `docs/coding-convention/specs/YYYY-MM-DD-<topic>-design.md`, unless the user specified another location or prohibited an artifact. Follow the task's commit policy.
7. Review the specification for missing requirements, contradictions, ambiguous behavior, unnecessary features and boundaries too broad for one plan. Correct in-scope defects, retaining already accepted decisions.
8. Invoke writing-plans to turn the accepted design into implementation tasks. Brainstorming itself does not invoke an implementation skill.

## Design boundaries

Each component needs a named responsibility, interface and dependency set. In an existing project, follow its established patterns. Include structural improvement when the observed defect affects the requested work; do not add unrelated refactoring.

Contrary evidence can require revisiting a requirement, component or data-flow assumption. Identify the affected decision, revise it within existing authority and verify that correction. Boundary-contract owns any change requiring new human authority.

Use [references/spec-document-reviewer-prompt.md](references/spec-document-reviewer-prompt.md) when an independent specification review can resolve an uncertainty the self-review leaves. Do not dispatch a reviewer merely to repeat an unchanged approval.

## Visual companion

Offer the browser companion once when a forthcoming design decision benefits from mockups, layouts or diagrams. Present the offer alone, state that it opens a local URL and may consume substantial tokens, and wait for the user's choice. A decline keeps this companion disabled; use text for the design discussion.

If the user accepts, read [references/visual-companion.md](references/visual-companion.md) before starting it. Decide per question whether a visual helps: layout comparisons fit the browser, while requirements and textual trade-offs normally fit the conversation. Consent to the companion does not require every question to use it.

## Failure modes and output

Do not treat a simple task as exemption from a real unresolved design decision. Do not silently narrow the objective.

The output is the accepted specification and any exact unresolved decision, followed by writing-plans when its prerequisites are available. Verification-before-completion governs claims that the specification is complete.
