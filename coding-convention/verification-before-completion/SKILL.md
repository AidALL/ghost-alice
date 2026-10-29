---
name: verification-before-completion
description: Before a new completion, working-fix, success or verified-status claim or a recommendation the user will act on, checks it against its criterion and evidence and decides whether evidence must be reread.
compatibility:
  - "Python 3.11+ standard library"
---

# Verification Before Completion

A delegated assistant reviews its assigned evidence and returns the result to its parent. The assistant answering the current conversation performs route registration, response review and final delivery.

The current-session assistant follows this instruction document to judge claims before a new completion, fix, success, verified-status or material recommendation claim, and before rereading an artifact to support a claim. Match every claim to the user's actual criterion and evidence. A printed success label, another agent's report or a control block is not proof.

## Acceptance criteria and claim scope

Extract the requested result and accepted constraints from user intent and the active contract. Compare the proposed result with the original sources and required criteria, including how the verified parts support the whole requested outcome. Reconsider a selected reference or interpretation when contrary evidence challenges it; do not rewrite the source or user criterion to make the result pass.

For each proposed claim, identify the criterion, checked target and failure context, observation method, source locator and original verification time. Distinguish source conformance, executable regression, installed-copy delivery and behavior in an ongoing conversation. Evidence for one stage supports only that stage. A source edit or installation does not demonstrate behavioral improvement; a case passed by both baseline and candidate does not establish that the candidate caused improvement.

Honor agreed disposition criteria. If the user agreed that a historical issue is handled when a selected current check does not reproduce it, judge that condition rather than inventing a universal-reliability requirement.

A verdict you endorse is your claim, including a reviewer's verdict. A new absence claim requires a search that could find the missing object; reuse an earlier search only when its target and criterion remain unchanged. Severity and confidence never replace evidence.

## Verify-or-reuse gate

Before inspecting an artifact again, apply [references/verify-or-reuse.md](references/verify-or-reuse.md). The executable helper is `scripts/verify_or_reuse.py`, resolved relative to this skill.

## Evidence selection and stop gate

Before a discretionary check, the agent identifies the unresolved criterion or competing explanation, why the method distinguishes the alternatives, and which attainable outcomes would change a specific next action or acceptance judgment. If no outcome can change that decision, do not run the check. For stochastic repetition, define the uncertainty, sampling rule and stopping condition before repeating.

Mandatory checks are those that the user or the project requires; they remain required. A check that a skill lists is required only when its outcome can change the next action or a criterion's judgment. An unavailable required observation leaves its criterion unresolved. An inconclusive check supplies no business pass/fail verdict, although it consumes the resources used to run and record it.

Choose evidence for the claim: content or behavior for semantic claims; hashes or provenance for identity, integrity, drift, merge safety or reproducibility. Hash equality cannot establish behavior. A tool's actual action result may itself satisfy the criterion; do not run a second check merely to obtain another output.

When an already-executed action has an unknown outcome, inspect the affected result under a bounded observation contract instead of automatically executing the action again. Required work that has not run still needs its actual execution. Preserve unchanged valid proof with its original source and time.

Systematic-debugging owns [incident accounting and continuation](../systematic-debugging/SKILL.md#incident-accounting-and-continuation). Boundary-contract owns authority and resource boundaries.

## Finalization sequence

For a new current-turn closure:

1. Load this skill through the host's real mechanism, or validly reuse its body under [Instruction Body Reuse](references/verify-or-reuse.md#instruction-body-reuse), and apply the workflow to this input.
2. Map each claim to its criterion and actual evidence. A successful result already returned in this turn can supply fresh evidence; run a further check only for a criterion it does not cover. Read the result, source locator, exit status and failures before judging it.
3. Follow session-intent-analyzer's `references/ledger-schema.md`, section Core Response Projection, for the exact current binding. Resolve that reference from the selected installed session-intent-analyzer directory.
4. Compare each claim with its original sources and criterion, then separately judge the complete draft against the user's objective and constraints. Follow [Prepare and record the response review](references/response-review.md#prepare-and-record-the-response-review) to submit both judgments to Core. Core authenticates and stores them; code does not decide their meaning.
5. Deliver the supported reviewed answer with its applicable completion or partial control under the owner reference's delivery procedure.

Explaining an unchanged prior result is not a new completion submission. Core selects completion processing from an actual completion/partial control marker; it does not infer completion from prose or selected execution work. The current-session assistant identifies and explicitly routes its new completion claims through this workflow. Never describe an old check as freshly executed.

## Retained original results and delivery

For a source-backed explanation, [Prepare and record the response review](references/response-review.md#prepare-and-record-the-response-review) owns both judgments; record them only when the user explicitly requests an authenticated explanation review. New completion and partial-completion claims follow the finalization sequence above.

For a correction to an already submitted answer, follow the review reference's [Deliver](references/response-review.md#deliver) procedure using the host's supplied visibility context. That procedure owns delivery of the reviewed answer and the Stop observation; the assistant does not infer visibility from an unrelated tool result.

## Retain evidence on first execution

Before a necessary action or check, arrange to retain its original result, exit status, source locator and verification time. Keep an orchestrated tool result in session state when later steps need the original object.

If the result is already available, cite it or copy its unchanged contents into permitted storage. Do not rerun the action to populate a variable, add redirection or create an evidence file. Missing storage is not missing verification. If the original result is lost or incomplete, state that gap and use verify-or-reuse to decide the affected observation.

To record proof, follow the review reference's [Proof bytes and time](references/response-review.md#proof-bytes-and-time).

## Completion-Check Format

The control's `scope` describes completion evidence coverage. In `[partial-check]`, `scope: partial` means that declared required criteria remain unresolved. It is independent of the focus named in the answer's `[focus: …]` line, which selects the judgment target (`micro`, `meso`, `macro` or `meta`). A macro judgment can therefore have partial evidence coverage. The assistant preserves both meanings when preparing the answer; it does not substitute a focus value for the required partial control.

For a new full closure, place `[completion-check]` before the supported summary. Declare every acceptance criterion and map each claim to its criterion, evidence and bare `pass` or `fail` verdict. Put `claim`, `criterion`, `evidence` and `verdict` on separate physical lines. A full block requires `unverified: none`; a failed verdict proves failure, not overall success.

For supported partial success, use `[partial-check]` with `scope: partial`, the same criterion/claim mapping and a nonempty `unverified` list. Each unresolved entry names a declared criterion as `criterion-id: reason`. Preserve all required criteria instead of narrowing the contract to the passing subset. Plain partial status without a successful-work claim does not need a success proof block.

The same proof format applies on every host. Core completion acceptance authenticates the per-claim and whole-answer judgments under the current owned route, as specified in [Prepare and record the response review](references/response-review.md#prepare-and-record-the-response-review). Skill loading and reuse remain workflow and audit facts; a Skill invocation or self-report field is neither completion evidence nor a separate acceptance prerequisite.

The examples below illustrate serialization only; replace their claims and source locators with actual results. They do not assert that any example check ran.

Full result:

```text
[completion-check]
- acceptance-criteria:
  - target: The requested behavior passes its regression [source: user-said]
- claim-evidence-map:
  - claim: The requested behavior passed its regression
    criterion: target
    evidence: Original regression output with source locator
    verdict: pass
- unverified:
  - none
- evidence: Original regression output with source locator
```

Partial result:

```text
[partial-check]
- scope: partial
- acceptance-criteria:
  - target: The requested behavior passes its regression [source: user-said]
  - installed: The installed behavior is verified [source: user-said]
- claim-evidence-map:
  - claim: The requested behavior passed its regression
    criterion: target
    evidence: Original regression output with source locator
    verdict: pass
- unverified:
  - installed: Installed observation is unavailable
- evidence: Original regression output with source locator
```

Retain actual body-load/reuse facts in the internal audit. The common contract's io-trace Transparency Rule governs what the answer shows.

The review reference's [Deliver](references/response-review.md#deliver) procedure owns delivery of the reviewed answer. The control block alone establishes neither application nor continuation.

## Confirmed flaw propagation

For a confirmed flaw in the requested deliverable, apply [Confirmed Flaw Propagation](references/verify-or-reuse.md#confirmed-flaw-propagation).

## External evidence and evaluator requirements

For a material external tool or platform claim, apply [references/external-tool-evidence.md](references/external-tool-evidence.md), including its specification/runtime distinction, source locators and explicit exemptions.

Before claiming a result that adversarial-verification reviewed at level-3-high-stakes, or claiming external governance absorption or evaluator-candidate promotion, apply [references/evaluator-artifacts.md](references/evaluator-artifacts.md). Missing or rejected required evaluator proof blocks that claim or promotion.

## Failure modes and partial continuation

A changed diff is not proof of a fix; lint is not proof of a build. [references/verification-detail.md](references/verification-detail.md) gives criterion-specific examples.

While supported authorized work remains, report partial status in commentary and continue it. Stop dependent work for an explicit stop, missing required decision, exhausted resources or unavailable prerequisite, and report that precise limitation.

Before finalizing, verify that every claimed result has a criterion and evidence, every unresolved required criterion remains visible, no claim exceeds its checked stage, and no unchanged action was replayed merely for recording.
