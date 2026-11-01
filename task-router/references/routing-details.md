# Routing Details

The response model uses this reference when boundary reconciliation is uncertain or a supplied phrase leaves the next action uncertain. `../SKILL.md` owns the route procedure and schemas.

## Boundary reconciliation

The response model compares current human instructions and supplied event/conversation evidence with retained goals, scope, decisions and constraints. A clear current instruction can authorize a bounded change without formal revocation wording. A decision timestamp does not date an undated restriction; admission, prior edits or a compressed goal do not establish replacement chronology.

If only conflicting snapshot fields remain, the response model leaves the conflict unresolved and asks for the exact contested decision. It must not call one field obsolete without evidence. Missing task files do not resolve authority.

## Direct answer and confirmation conditions

The common contract's Work Selection section carries task-router's direct-answer rule and its no-tool action; its Session Intent section owns confirmation questions. Use the examples below when a supplied phrase leaves the next action uncertain.

A general why/how premise remains an explanation topic unless the user identifies a workspace, supplies workspace evidence, requests exact diagnosis, or the conversation establishes the inspected referent. First-person wording, past tense, technical-state vocabulary and an open project do not independently establish inspection authority. An established referent permits inspection but does not require it when retained evidence answers the question.

## Direct-answer actions and the agent visibility profile

Follow the common contract's Work Selection section for a direct answer and its Session Intent section for a confirmation question. For work, task-router owns route registration and verification-before-completion owns closure claims; an instruction from the agent visibility profile does not replace either workflow.

## Examples

| Supplied situation | Decision and next action |
| --- | --- |
| An authorized parser fix is unfinished; the user asks for status | Answer from retained evidence in commentary, then implement/test under existing authority |
| One provider's fixed quota blocks its smoke test, but an authorized local path remains | Preserve the provider-specific gap, continue independent work and do not probe the unchanged quota |
| The user says “Stop editing and explain the existing result” | Stop edits and answer from retained evidence; historical open flags do not restart work |
| “I cannot get it to work” with no established referent | Ask the minimum question about the error/context; do not inspect the ambient project |
| A stable compile-only how-to with no version-specific claim | Give the bounded method directly |
| A general question about a past process state without an established inspected artifact | Explain common causes without diagnosing the ambient workspace |
| “Check the requirements table against the body” | Normal verification; boundary-contract establishes comparison scope before extraction |
| “Build only the Android login mockup; do not touch auth/API/DI/navigation; verify a screenshot” | Normal development; boundary-contract preserves prohibited surfaces and visual criterion before coding |
| “Schedule a meeting tomorrow at 3” | Normal operations; boundary-contract captures the external side effect before scheduling |

The examples illustrate classification, not findings about any current tool or workspace.

## Failure modes and repair

The response model must not infer authorization from metadata, use no-work labels to abandon implementation, or claim an untested branch passed because another branch passed.

Authority and renewal belong to `../../boundary-contract/SKILL.md#authority-and-renewal`. Incident accounting belongs to `../../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation`. Proof reuse, discretionary checks and native delivery belong to `../../coding-convention/verification-before-completion/SKILL.md`. A new input or route error reopens the affected binding, not unrelated business proof. A genuinely expanded release-readiness claim requires evidence for the added conditions.
