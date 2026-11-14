# Session Gate Matrix

[🇺🇸 English](./session-gate-matrix.md) | [🇰🇷 한국어](../ko/policies/session-gate-matrix.md)

This page maps each condition to its procedure owner. The [architecture](../../architecture.md) defines component responsibilities; the linked skills define the response model's actions.

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
- [Notes](#notes)
- [Completion Recording Contract](#completion-recording-contract)
- [Current Selection and Bounded Recovery](#current-selection-and-bounded-recovery)
- [Repair and authority conditions](#repair-and-authority-conditions)


## Required Entrypoints

| Condition | Response model procedure |
| --- | --- |
| Every prompt the host delivers under the user role | [Common contract: Session Intent](../../platforms/common-governance.md#session-intent) for interpretation; [session-intent-analyzer](../../session-intent-analyzer/SKILL.md) for the record |
| The response model selects boundary-contract: required | boundary-contract before dependent work |
| Development work begins | coding-convention/using-coding-convention |
| A bug needs investigation | coding-convention/systematic-debugging |
| The human user's requirement or the source contract states the production behavior, and a test can exercise it | coding-convention/test-driven-development |
| Executed-work completion or partial-success claim | coding-convention/verification-before-completion |
| Commit/push or branch integration | coding-convention/finishing-a-development-branch |
| The response model proposes a new task, file, audit cycle or follow-up, or the human user asks whether to do one | necessity-gate |

## Turn Routing Contract

[merge-companion](../../merge-companion/SKILL.md#entry-and-activation) sets when the response model checks the pending-merge manifest and reviews its entries.

When the human user asks for a skill-evolution report or authorizes that analysis, the response model may separately apply skill-evolution to the same exact session; this procedure neither feeds task-router nor dispatches work.

The response model follows [task-router](../../task-router/SKILL.md) to select work and construct the route candidate, and [boundary-contract](../../boundary-contract/SKILL.md) to declare its scope within the work that the human user's instructions authorize. Neither procedure grants host permission.

## Session Intent Ledger Contract

Core code records the current input under [Canonical intake provenance](../../session-intent-analyzer/references/ledger-schema.md#canonical-intake-provenance), which owns host-specific input selection, pending-input resolution and failure handling. The response model records changed interpretations and correction provenance under [session-intent-analyzer](../../session-intent-analyzer/SKILL.md).

Runtime consumers use the public API with the exact session metadata root/platform/session and the current input that Core code recorded in the intent ledger. The intent ledger's SQLite database is the primary store; intent-state.json and intent-events.jsonl are import/export forms, and current-session.json is an advisory pointer. None supplies a substitute write identity. Missing recording leaves dependent operations unresolved. The response model selects recovery only when the human user's objective or a required dependency needs it and the human user's instructions authorize it; it never guesses identity or invents permission.

The [ledger schema](../../session-intent-analyzer/references/ledger-schema.md) owns state fields, transaction rules, migration hashes and current response projection. Intake events and intent-state fields do not store raw prompts, full conversations or tool outputs, system/developer instructions or secrets. As the response-review procedure specifies, Core code stores the response model's answer, original-source snapshots and the response model's review judgments in separate Core review tables (`core_completion_candidates`, `core_completion_assessments`, `core_completion_responses`); those review records are distinct from the response model's `interpreted-as` record. Hookless work follows the same identity contract; a direct answer uses no tools.

## Runtime Hook Graph Contract

1. The session-intent-analyzer SessionStart hook supplies the session metadata. Its hook for the host's prompt-submit event (`UserPromptSubmit`) observes each prompt the host delivers under the user role and adds model context only when the session metadata was not delivered or intake failed.
2. The response model submits the normal route candidate to Core code. Core code validates the current input that Core code recorded, ownership, the `selectable` flags and selected acceptance criteria before storing the ready projection. An acceptance criterion's `selectable` flag is set by Core code's default from its source label or by the response model's explicit value; the response model declares each work unit's `selectable` flag. A true flag makes the item selectable and does not prove the human user's approval.
3. The response model follows any required boundary and owning workflow. [Responsibilities and flow](../../architecture.md#responsibilities-and-flow) assigns tool execution and permission enforcement to the host. The io-trace hook keeps recording tool operations in io-trace records (`~/.ghost-alice/io-trace.jsonl`). The host runs it on `PostToolUse` and `PostToolUseFailure` on Claude Code and on `PostToolUse` on Codex. On both hosts a call leaves no io-trace record when the host rejects it before running it because one of the host's permission rules or a hook registered outside the hook runner denied it, or an answer that denies it was entered at the host's permission prompt. Nor does a call leave such a record when the human user cancels it while it runs. On Claude Code a rejected call includes an `Edit` whose `old_string` the file does not hold. On Codex a shell command that exits non-zero leaves an io-trace record, but another failed call does not, such as a failed `apply_patch` or MCP call or a shell command that cannot start, and neither does a server-side tool such as WebSearch.
4. When an answer ends and Stop fires, Core code's Stop script runs as [Hook calls](../../architecture.md#hook-calls) describes.

Tool-checkpoint runs at the before-tool stage (host `PreToolUse`/`BeforeTool`) and is not Core code's input intake. Tool payloads and audit metadata do not grant native permission. The [Notes](#notes) list installed bytes and native execution as separate claims; [live-smoke-regression](live-smoke-regression.md) owns required runtime observations.

## Dynamic Focus Contract

The response model selects the subject of judgment from micro, meso, macro and meta as [Responsibilities and flow](../../architecture.md#responsibilities-and-flow) defines them. When investigation is needed, evidence may move focus wider, narrower or to another target at the same scope. Meta review does not itself require a user question.

When work selection must change, the response model applies task-router again using the affected result and contrary evidence. It compares the result with its relevant reference and investigates the cause far enough to select a correction only when the human user's objective or a required dependency needs it and the human user's instructions authorize it. It can retain an unresolved detail or omit nonessential work when the human user's objective and required dependencies are unaffected; it does not create a recovery task merely because a mismatch exists. Required criteria still need positive support, including for the combined result. [Evidence selection and stop gate](../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate) sets which checks are required and when an additional check runs. The static calls graph does not prescribe repeated runtime checks.

## Routing Surface Contract

The response model applies session-intent-analyzer to record the objective (`current_goal`), scope (`latest_scope`), constraints, decisions, open questions and acceptance criteria in the intent ledger through the Python ledger writer. Each item's source label names its origin; an item without a source label has no recorded origin. It applies task-router to make the work judgment. The routing surface carries only the response mode, the boundary-contract control and an optional reason; it carries no depth or complexity value. Unknown or conflicting values require reconsideration of the affected selection.

The response model applying task-router preserves what the human user primarily asked for and the human user's terminal objective. Investigation and worktree inspection remain means unless the human user requested them as deliverables.

[Routing details](../../task-router/references/routing-details.md) separate a direct answer and a confirmation question from work. A general why/how premise and ambient workspace do not authorize local diagnosis. A confirmation question takes the form the common contract's Session Intent section sets. The contract's [Work Selection](../../platforms/common-governance.md#work-selection) section sets the direct-answer condition: the answer uses no tool and registers no route. That condition does not prohibit the response route registration that task-router requires on a work completion path.

## Work-Impact Projection Contract

Hooks classify values by whether they change what the response model declares in its boundary contract within the work that the human user's instructions authorize, the focus, the verification burden or the recovery.

- Forced/risk/gate values and failed verification retain full model hints and forced visibility.
- Routine values remain in the hook log (`~/.ghost-alice/session-logs/<host>/<session>/strict-hook-output.jsonl`) and enter model guidance only when they affect a work decision.
- Unknown or failed values expose fuller context and reopen the affected judgment.

Clean-pass notices and correlation metadata do not independently justify new work.

## Runtime Checkpoints

Decision-relevant routing guidance is owned by [task-router](../../task-router/SKILL.md#referenced-contracts).

[Verification-before-completion](../../coding-convention/verification-before-completion/SKILL.md#completion-check-format) owns the shared completion-check and partial-check formats and their criterion/claim/evidence mapping. Core completion acceptance requires authenticated review of the claims, original sources and complete response under the current owned route. Skill loading remains workflow guidance and audit activity, not a separate acceptance prerequisite. Finalized completion requires supported verdicts with no unverified criteria. A partial-success report retains the full contract and identifies unresolved criteria; it cannot present the successful subset as whole-task completion.

A displayed trace does not establish proof. Hooks keep audit records regardless of the display, and the response model supports each claim with actual workflow evidence.

## tool-checkpoint Visible Surface

The common contract's io-trace Transparency Rule owns checkpoint visibility.

## Notes

Procedure ownership prevents duplicated rules from drifting. The response model follows [instruction-body reuse](../../coding-convention/verification-before-completion/references/verify-or-reuse.md) and records actual reads or valid reuse internally. Routine clean states need no repeated prose; pending, changed and failed states retain their decision-relevant notice.

Source edit, named test pass, installed bytes, native execution, recorded review and visible answer are separate claims. Evidence for one stage cannot establish another.

## Completion Recording Contract

For a new completion or partial submission, Core Python code checks the current owned route before proof formatting and authenticated semantic review. A missing route therefore returns the route defect before requesting corrected proof fields.

For this condition, the response model records both judgments using the [owning review procedure](../../coding-convention/verification-before-completion/references/response-review.md#prepare-and-record-the-response-review). The [Core response flow](../../architecture.md#evidence-and-native-delivery) explains authentication and native answer delivery; [output semantics](tool-output-semantics.md#review-output-and-continuation) distinguishes supported and unsupported results.

Source APIs, runtime modules, ledger, project, installed skills and platform state retain separate location bindings. An ordinary upgrade keeps the intent ledger's SQLite database as the primary store; transport does not grant origin, session, selectability or approval.

[Hook calls](../../architecture.md#hook-calls) describes the native Stop path. Supported, unsupported and unresolved states stay distinct, and a supported report may fulfill no work criterion.

## Current Selection and Bounded Recovery

Core code validates the current response route supplied by the response model following task-router. The next input that Core code records changes the route binding while preserving stable work units and unchanged original evidence.

A named route, proof or recording defect returns to its procedure owner below. The response model chooses correction only when the human user's objective or a required dependency needs it and the human user's instructions authorize it. Unsupported claims are revised or withdrawn; the answer reports a required unresolved criterion as unresolved. An identical unchanged recording failure suspends that recording attempt without replaying business work or blocking independent work that the human user's instructions authorize.

[Review and delivery](../../coding-convention/verification-before-completion/references/response-review.md) owns the calling sequence. Core's retained-report path applies only when the original proof supports the exact entire statement; added prose or a broader claim needs its own review.

## Repair and authority conditions

- [Boundary-contract: Authority and renewal](../../boundary-contract/SKILL.md#authority-and-renewal) owns the authority that the human user's instructions grant, allowed targets and resource limits.
- [Systematic-debugging: Incident accounting and continuation](../../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation) owns failed repair rounds, reassessment and bounded continuation.
- [Verification-before-completion: Evidence Selection And Stop Gate](../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate) owns useful checks and proof reuse.
- [Response review](../../coding-convention/verification-before-completion/references/response-review.md) owns the review record, delivery and recording recovery.

These owners preserve cumulative attempts, original source/time and unaffected criteria. A branch-local failure blocks its dependencies, not unrelated work that the human user's instructions authorize.
