# Session Gate Matrix

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/session-gate-matrix.md)

The machine gate index is [skill-catalog/session-gates.json](../../skill-catalog/session-gates.json). This page maps each condition to its procedure owner. The [architecture](../../architecture.md) defines component responsibilities; the linked skills define agent actions.

## Contents

- [Required Entrypoints](#required-entrypoints)
- [Turn Routing Contract](#turn-routing-contract)
- [Session Intent Ledger Contract](#session-intent-ledger-contract)
- [Runtime Hook Graph Contract](#runtime-hook-graph-contract)
- [Dynamic Focus Contract](#dynamic-focus-contract)
- [Routing Surface Contract](#routing-surface-contract)
- [Work-Impact Projection Contract](#work-impact-projection-contract)
- [Runtime Checkpoints](#runtime-checkpoints)
- [tool-checkpoint Visible Surface](#tool-checkpoint-visible-surface)
- [tool-checkpoint Batch / Continuation Compression](#tool-checkpoint-batch--continuation-compression)
- [Notes](#notes)
- [Completion Recording Contract](#completion-recording-contract)
- [Current Selection and Bounded Recovery](#current-selection-and-bounded-recovery)
- [Repair and authority conditions](#repair-and-authority-conditions)


## Required Entrypoints

| Condition | Agent procedure |
| --- | --- |
| Every user input | Native intake observation, then interpretation; work with a new or changed interpretation records `interpreted-as: ` before its first tool call; stored intent is read when the decision depends on it |
| The current-session assistant selects boundary-contract: required | boundary-contract before dependent work |
| Development work begins | coding-convention/using-coding-convention |
| A bug needs investigation | coding-convention/systematic-debugging |
| Production behavior is known and testable | coding-convention/test-driven-development |
| Executed-work completion or partial-success claim | coding-convention/verification-before-completion |
| Commit/push or branch integration | coding-convention/finishing-a-development-branch |
| A new task, file, audit cycle or follow-up is defined | necessity-gate |

## Turn Routing Contract

[merge-companion](../../merge-companion/SKILL.md#entry-and-activation) sets when the current-session assistant checks the pending-merge manifest and reviews its entries.

The current-session assistant routes every input, including a follow-up or status question, using current intent and continuing authority. A prior route is not the current input's route. The current-session assistant may separately apply skill-evolution to the same exact session for a report; this procedure neither feeds task-router nor dispatches work.

The current-session assistant follows [task-router](../../task-router/SKILL.md) to select work and construct the route candidate. It follows [boundary-contract](../../boundary-contract/SKILL.md) to declare allowed targets, exclusions and resources; neither procedure grants native permission.

## Session Intent Ledger Contract

Core records canonical human input as a digest, character count and ordered event without persisting raw input. Codex intake commits directly. Claude UserPromptSubmit stages digest/count and native correlation; the trusted PreToolUse or Stop wrapper reconciles appended native actor rows. Human input, including input typed while Claude works, advances canonical authority. An SDK-started turn or an app event delivered while Claude works becomes non-human context without authority. Task notifications and subagent hand-backs retain the last human route. A later located human input supersedes an unknown or unlocated observation; without one the observation remains pending and dependent writes stay refused. The PreToolUse or Stop wrapper records each such wait and warns the user once per session and cause. A failed reconcile is recorded, and the inner hook still runs. The current-session assistant records compressed semantic changes only when goals, constraints, decisions, questions or criteria materially change.

Runtime consumers use the public API with the exact session-receipt root/platform/session and the current input from the ledger. SQLite is authoritative; intent-state.json and intent-events.jsonl are import/export forms, and current-session.json is an advisory pointer. None supplies a substitute write identity. Missing recording leaves dependent operations unresolved. The assistant selects recovery only when needed for the objective or a required dependency and within existing authority; it never guesses identity or invents permission.

[Session-intent-analyzer](../../session-intent-analyzer/SKILL.md) owns intake and correction provenance. Its [ledger schema](../../session-intent-analyzer/references/ledger-schema.md) owns state fields, transaction rules, migration hashes and current response projection. Intake events and intent-state fields do not store raw prompts, full conversations or tool outputs, system/developer instructions or secrets. Separate Core review records retain authorized answer and original-source snapshots and the model’s review judgments; those review records are not session-intent summaries. Hookless work follows the same identity contract; a direct answer uses no tools.

## Runtime Hook Graph Contract

1. The session-intent-analyzer SessionStart hook supplies the session receipt. The UserPromptSubmit hook records each input and adds model context only when that receipt was not delivered or intake failed. Core intake establishes exact canonical human input through direct Codex observation or trusted Claude reconciliation. Read-state remains a pure read. The current-session assistant applies task-router to select work; it separately applies skill-evolution when report-only analysis is required.
2. The current-session assistant submits the normal route candidate to Core. Core Python code validates current input, ownership, admission and selected criteria before storing the ready projection.
3. The current-session assistant follows any required boundary and owning workflow. [Responsibilities and flow](../../architecture.md#responsibilities-and-flow) assigns tool execution and permission enforcement to the native host. PostToolUse retains strict audit.
4. At completion, the Stop observer calls Core's input/body-bound assessment and records the result; it blocks nothing.

Tool-checkpoint runs at the before-tool stage (host `PreToolUse`/`BeforeTool`) and is not user-input intake. Tool payloads and audit metadata do not grant native permission. The [Notes](#notes) list installed bytes and native execution as separate claims; [live-smoke-regression](live-smoke-regression.md) owns required runtime observations.

## Dynamic Focus Contract

The current-session assistant selects the subject of judgment from micro, meso, macro and meta as [Responsibilities and flow](../../architecture.md#responsibilities-and-flow) defines them. When investigation is needed, evidence may move focus wider, narrower or to another target at the same scope. Meta review does not itself require a user question.

When work selection must change, the current-session assistant applies task-router again using the affected result and contrary evidence. It compares the result with its relevant reference and investigates the cause far enough to select any needed authorized correction. It can retain an unresolved detail or omit nonessential work when the objective and required dependencies are unaffected; it does not create a recovery task merely because a mismatch exists. Required criteria still need positive support, including for the combined result. [Evidence selection and stop gate](../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate) sets which checks are required and when an additional check runs. The static calls graph does not prescribe repeated runtime checks.

## Routing Surface Contract

The current-session assistant applies session-intent-analyzer to express semantic facts and decisions and submits them to the Python ledger writer. It applies task-router to make the reusable work judgment. The routing surface carries only the response mode, the boundary-contract control and an optional reason; it carries no depth or complexity value. Unknown or conflicting values require reconsideration of the affected selection.

The current-session assistant applying task-router preserves the primary request and terminal objective. Investigation and worktree inspection remain means unless requested as deliverables.

[Routing details](../../task-router/references/routing-details.md) separate a direct answer (`direct-response`) and a confirmation question (`clarification-only`) from work. A general why/how premise and ambient workspace do not authorize local diagnosis. A confirmation question takes the form the common contract's Session Intent section sets. The contract's [Work Selection](../../platforms/common-governance.md#work-selection) section sets the direct-answer condition: the answer uses no tool and registers no route. That condition does not prohibit authorized route registration on a work completion path.

## Work-Impact Projection Contract

Hooks classify values by whether they change the work boundary, focus layer, verification burden, or recovery.

- Forced/risk/gate values and failed verification retain full model hints and forced visibility.
- Routine values remain in the strict log and enter model guidance only when they affect a work decision.
- Unknown or failed values expose fuller context and reopen the affected judgment.

Clean-pass notices and correlation metadata do not independently justify new work. Token reduction is a consequence, not a success metric.

## Runtime Checkpoints

Decision-relevant routing guidance is owned by [task-router](../../task-router/SKILL.md#referenced-contracts). The current-session assistant records a skill call only after using a fresh or valid retained instruction body and following that workflow; metadata matching is not execution.

[Verification-before-completion](../../coding-convention/verification-before-completion/SKILL.md#completion-check-format) owns the shared completion-check and partial-check formats and their criterion/claim/evidence mapping. Core completion acceptance requires authenticated review of the claims, original sources and complete response under the current owned route. Skill loading remains workflow guidance and audit activity, not a separate acceptance prerequisite. Finalized completion requires supported verdicts with no unverified criteria. A partial-success report retains the full contract and identifies unresolved criteria; it cannot present the successful subset as whole-task completion.

A displayed trace does not establish proof; strict internal audit and actual workflow evidence remain required.

## tool-checkpoint Visible Surface

The common contract's io-trace Transparency Rule owns checkpoint visibility.

## tool-checkpoint Batch / Continuation Compression

Hooks check every call and preserve strict audit while suppressing unchanged repeated user-facing blocks in the same input lineage. Batch and continuation forms do not grant permission.

Continuation refers only to polling the same process/session/tool-call ID. A denial, mismatch or relevant state change can require a new checkpoint. Polling and a new input alone do not require repeating guidance.

## Notes

Procedure ownership prevents duplicated rules from drifting. The current-session assistant follows [instruction-body reuse](../../coding-convention/verification-before-completion/references/verify-or-reuse.md) and records actual reads or valid reuse internally. Routine clean states need no repeated prose; pending, changed and failed states retain their decision-relevant notice.

Source edit, named test pass, installed bytes, native execution, recorded review, met criterion and visible answer are separate claims. Evidence for one stage cannot establish another.

## Completion Recording Contract

For a new completion or partial submission, Core Python code checks the current owned route before proof formatting and authenticated semantic review. A missing route therefore returns the route defect before requesting corrected proof fields.

For this condition, the responding model records both judgments using the [owning review procedure](../../coding-convention/verification-before-completion/references/response-review.md#prepare-and-record-the-response-review). The [Core response flow](../../architecture.md#evidence-and-native-delivery) explains authentication and native answer delivery; [output semantics](tool-output-semantics.md#review-output-and-continuation) distinguishes supported and unsupported results.

Source APIs, runtime modules, ledger, project, installed skills and platform state retain separate location bindings. An ordinary upgrade preserves ledger authority; transport does not grant origin, session, admission or approval.

On the native Stop path, Core's observer validates the delivered text against the recorded review and keeps the result in strict audit; it issues no second command and holds no output. Supported, unsupported and unresolved states stay distinct: an unresolved criterion remains unmet, and a supported report may fulfill no work criterion.

## Current Selection and Bounded Recovery

Core validates the current response route supplied by the model following task-router. A direct-answer or confirmation projection selects no execution units; a requested lookup is work. New input changes the route binding while preserving stable work units and unchanged original evidence.

A named route, proof or recording defect returns to its procedure owner below. The model chooses correction only when needed for the objective or a required dependency and within authority. Unsupported claims are revised or withdrawn; required unresolved criteria stay unmet. Identical unchanged recording failure parks dependent application without replaying business work or blocking independent authorized work.

[Review and delivery](../../coding-convention/verification-before-completion/references/response-review.md) owns the calling sequence and correction shape. [Tool output semantics](tool-output-semantics.md#review-output-and-continuation) names the returned selectors. Core's retained-report path applies only when original applied proof supports the exact entire statement; added prose or a broader claim needs its own review.

## Repair and authority conditions

- [Boundary-contract: Authority and renewal](../../boundary-contract/SKILL.md#authority-and-renewal) owns human authority, allowed targets and resource limits.
- [Systematic-debugging: Incident accounting and continuation](../../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation) owns failed repair rounds, reassessment and bounded continuation.
- [Verification-before-completion: Evidence Selection And Stop Gate](../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate) owns useful checks and proof reuse.
- [Response review](../../coding-convention/verification-before-completion/references/response-review.md) owns the review record, delivery and recording recovery.

These owners preserve cumulative attempts, original source/time and unaffected criteria. A branch-local failure blocks its dependencies, not unrelated authorized work.
