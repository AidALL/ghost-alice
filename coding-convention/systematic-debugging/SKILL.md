---
name: systematic-debugging
description: Before a fix is proposed for a failure whose cause and sufficient correction depth need investigation, tests causal hypotheses and corrects the supported cause; also counts failed repair rounds against the repair limit.
compatibility:
  - "Python 3.11+ standard library"
---

# Systematic Debugging

The implementing agent investigates an observed failure, tests a causal hypothesis and corrects the supported cause. Apply this workflow before proposing a fix for a failure whose cause and sufficient correction depth need investigation, and for repair limits. A symptom alone does not identify its cause.

## Investigation

Read the complete error and relevant trace. Retain the failure's command or trigger, target, source locator and observed result. Reproduce only when the existing evidence cannot settle the next causal decision; verification-before-completion owns the useful-check decision.

Inspect changes and dependencies that could produce the observed result. Give the largest supported cause priority without declaring other plausible causes resolved. In a multi-component system, inspect the input, output and configuration at the relevant component boundaries. Record presence or safe metadata for credentials; never print secrets.

Trace a wrong value backward to the caller or source that created it. Use [references/root-cause-tracing.md](references/root-cause-tracing.md) for this technique. Compare the failing path with a relevant working implementation, including its dependencies and preconditions. Read enough of the reference to understand the behavior being adopted; do not copy a fragment whose contract remains unknown.

## Hypothesis and correction

State the proposed cause and the observation supporting it. Choose a discriminating check that can distinguish that explanation from its live alternative. Change one causal variable for the experiment. If the result contradicts the hypothesis, update the hypothesis before attempting another correction.

Once expected behavior and the production path are known, use test-driven-development to reproduce the failure before editing production code. Correct the identified cause to sufficient depth, including structural changes when the causal evidence requires them. Exclude unrelated refactoring.

Verify the original failure and affected adjacent behavior. Preserve passing evidence for unaffected criteria. If the correction fails, identify which causal assumption, artifact mapping or design decision the result challenges and return to that point of investigation.

If evidence identifies an environmental or external failure that cannot be removed within this task, record the observed limitation and implement only authorized handling justified by that failure. An unexplained failure does not justify adding retries, monitoring or logging indiscriminately.

## Incident accounting and continuation

This section owns failed-round accounting for the family. The assistant investigating the failure records the unresolved failure, affected criterion, stage and target in its existing task context or handoff to the next assistant. Business failures and evidence-recording failures are separate incidents even when their error text matches.

The initial observed failure counts as zero failed repair rounds. Increment the count only after a corrective change and a completed relevant observation show that the same failure remains unresolved. A failed diagnostic, unavailable infrastructure or inconclusive observation consumes its actual resources but is not a failed business repair round.

Keep the incident's cumulative count across agents and handoffs. An unknown cause does not justify renaming the incident or resetting the count. Demonstrated resolution followed by recurrence creates a linked new incident.

After five failed repair rounds and before another correction attempt, the agent must complete the first premise reassessment: use the accumulated observations to reconsider the causal hypothesis, target mapping, design and verification method. Five is a maximum before that reassessment, not a minimum retry count or a limit on the whole task. Once the reassessment is recorded, counts six or seven do not alone demand another reassessment; new contrary evidence or an active stopping condition can.

To continue after reassessment, identify a distinguishable hypothesis, the observation that can discriminate it, an explicit additional-attempt or resource allowance, and a stopping condition. The allowance must fit the user’s remaining resource limit and, for a delegated task, the parent assistant’s remaining allocation to that task. Reassessment resets neither attempts nor consumed resources. Exhaustion suspends that incident's further attempts. Five failures alone do not require a human decision.

Boundary-contract owns authorization and renewal of agent-declared implementation limits. Ask the user only when continuation requires a human-owned decision or exceeds actual authority. Verification-before-completion owns useful checks, required evidence and proof reuse. Its response review reference owns recording-specific recovery: an identical unchanged recording failure suspends further recording attempts without waiting for five rounds.

Preserve already-supported business proof while recording is unavailable. Exclude suspended or human-waiting branches from executable work until a relevant dependency change or decision permits continuation; keep independent authorized work running. Keep these decisions in existing context and handoffs, without adding a compulsory report, counter hook or classifier.

## Failure modes

- Do not treat time pressure, familiarity or repeated error text as causal evidence.
- Do not add several speculative fixes and infer that the passing result identifies their cause.
- Do not call a correction successful from a changed diff or passing lint.
- Do not reset an incident count when handing the task to another agent.
- Do not replace the requested implementation with a diagnostic report.
- When the user challenges an assumption, compare that assumption with the recorded evidence and inspect only the affected uncertainty.

## Output and supporting techniques

Report the observed cause or remaining uncertainty, correction, criterion-specific evidence and incident status when it affects continuation. Verification-before-completion owns final completion and partial-control blocks.

Use [references/defense-in-depth.md](references/defense-in-depth.md) when evidence requires validation at multiple boundaries, and [references/condition-based-waiting.md](references/condition-based-waiting.md) when a timing failure requires waiting for an observable condition. Neither reference authorizes unrelated changes.
