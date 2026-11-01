---
name: systematic-debugging
description: Before a fix is proposed for a failure with an unclear cause, tests hypotheses, fixes the supported cause and counts failed fix attempts, reassessing the hypothesis after five.
compatibility:
  - "Python 3.11+ standard library"
---

# Systematic Debugging

The response model investigates an observed failure, tests a causal hypothesis and corrects the supported cause. Apply this workflow before proposing a fix for a failure whose cause and sufficient correction depth need investigation, and for repair limits. A symptom alone does not identify its cause.

## Investigation

Read the complete error and relevant trace. Retain the failure's command or trigger, target, source locator and observed result. Whether to reproduce follows verification-before-completion's [evidence selection gate](../verification-before-completion/SKILL.md#evidence-selection-and-stop-gate).

Inspect changes and dependencies that could produce the observed result. Give the largest supported cause priority without declaring other plausible causes resolved. In a multi-component system, inspect the input, output and configuration at the relevant component boundaries. Record presence or safe metadata for credentials.

Trace a wrong value backward to the caller or source that created it. Use [references/root-cause-tracing.md](references/root-cause-tracing.md) for this technique. Compare the failing path with a relevant working implementation, including its dependencies and preconditions. Read enough of the reference to understand the behavior being adopted; do not copy a fragment whose contract remains unknown.

## Hypothesis and correction

State the proposed cause and the observation supporting it. Choose a discriminating check that can distinguish that explanation from its live alternative. Change one causal variable for the experiment. If the result contradicts the hypothesis, update the hypothesis before attempting another correction.

Once expected behavior and the production path are known, use test-driven-development to reproduce the failure before editing production code. Correct the identified cause to sufficient depth, including structural changes when the causal evidence requires them. Exclude unrelated refactoring.

Verify the original failure and affected adjacent behavior. If the correction fails, identify which causal assumption, artifact mapping or design decision the result challenges and return to that point of investigation.

If evidence identifies an environmental or external failure that cannot be removed within this task, record the observed limitation and implement only authorized handling justified by that failure. An unexplained failure does not justify adding retries, monitoring or logging indiscriminately.

## Incident accounting and continuation

This section owns failed-round accounting for the family. The response model investigating the failure records the unresolved failure, affected criterion, stage and target in its existing task context or in its handoff. Business failures and evidence-recording failures are separate incidents even when their error text matches.

The initial observed failure counts as zero failed repair rounds. Increment the count only after a corrective change and a completed relevant observation show that the same failure remains unresolved. A failed diagnostic, unavailable infrastructure or inconclusive observation consumes its actual resources but is not a failed business repair round.

Keep the incident's cumulative count across agents and handoffs. An unknown cause does not justify renaming the incident or resetting the count. Demonstrated resolution followed by recurrence creates a linked new incident.

After five failed repair rounds and before another correction attempt, the response model must complete the first premise reassessment: use the accumulated observations to reconsider the causal hypothesis, target mapping, design and verification method. Five is a maximum before that reassessment, not a minimum retry count or a limit on the whole task. Once the reassessment is recorded, counts six or seven do not alone demand another reassessment; new contrary evidence or an active stopping condition can.

To continue after reassessment, identify a distinguishable hypothesis, the observation that can discriminate it, an explicit additional-attempt or resource allowance, and a stopping condition. The allowance must fit the user’s remaining resource limit and, for a delegated task, the parent’s remaining allocation to that task. Reassessment resets neither attempts nor consumed resources. Exhaustion suspends that incident's further attempts. Five failures alone do not require a human decision.

Boundary-contract owns authorization and the renewal of self-declared implementation limits. Verification-before-completion owns useful checks, required evidence and proof reuse. Its response review reference owns recording-specific recovery: an identical unchanged recording failure suspends further recording attempts without waiting for five rounds.

Exclude suspended or human-waiting branches from executable work until a relevant dependency change or decision permits continuation. Keep these decisions in existing context and handoffs, without adding a compulsory report, counter hook or classifier.

## Failure modes

- Do not treat time pressure, familiarity or repeated error text as causal evidence.
- Do not add several speculative fixes and infer that the passing result identifies their cause.
- Do not call a correction successful from a changed diff or passing lint.
- When the user challenges an assumption, compare that assumption with the recorded evidence and inspect only the affected uncertainty.

## Output and supporting techniques

Report the observed cause or remaining uncertainty, correction, criterion-specific evidence and incident status when it affects continuation. Verification-before-completion owns final completion and partial-control blocks.

Use [references/defense-in-depth.md](references/defense-in-depth.md) when evidence requires validation at multiple boundaries, and [references/condition-based-waiting.md](references/condition-based-waiting.md) when a timing failure requires waiting for an observable condition. Neither reference authorizes unrelated changes.
