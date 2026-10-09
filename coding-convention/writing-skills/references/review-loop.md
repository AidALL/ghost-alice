# Human Review Loop

This document defines bounded evidence-driven review for `writing-skills`. Human feedback resolves human-owned judgments; existing authority supports in-scope repairs.
## Contents

- [When to read this](#when-to-read-this)
- [Core principles](#core-principles)
- [Minimal loop](#minimal-loop)
- [Standard loop](#standard-loop)
- [Record format](#record-format)
- [Prohibited](#prohibited)
- [Tool usage principle](#tool-usage-principle)


## When to read this

- When you want to compare baseline and with-skill results side by side.
- When you are unsure what format to collect user feedback in.
- When you need to decide how far to attach assertions.

## Core principles

- Feedback is diagnostic information. It is not completion evidence.
- An evaluation without a baseline creates an illusion.
- Mix qualitative feedback with quantitative checks, but do not force quantification.
- After a fix, evaluate the affected condition. Retain unchanged valid checks and stop when no live uncertainty can change the decision.

## Minimal loop

1. Name the live uncertainty and relevant scenario or source contradiction.
2. Save the actual baseline result, including a pass.
3. Repair the affected instruction and complete official Phase 1 through Phase 5.
4. Evaluate the candidate on affected conditions and relevant negative controls.
5. Resolve observed mismatches within existing authority; ask only for a human-owned judgment.
6. Stop when another unchanged check cannot change the next decision.

Two passing variants support their checked conditions, not a claim of causal improvement.

## Standard loop

Turn only the objectifiable items below into assertions:
- A specific section must exist.
- A forbidden expression must not appear.
- The specified format must be followed.
- A specific tool or order must actually be used.

Items that are hard to objectify:
- A sentence is "more persuasive".
- It "looks good" overall.
- Creativity is sufficient.

Use qualitative judgment and evidence appropriate to these items. Ask the user only when the decision belongs to them or their preference is essential.

## Record format

For each scenario, leave at least the following:

```yaml
scenario: "rule compliance under fatigue plus time pressure"
baseline_result:
  verdict: fail
  observations:
    - "claims completion without verification"
with_skill_result:
  - "claims the result only after running the verification command"
user_feedback:
  - "improved, but still overconfident right before the deadline"
next_change:
  - "strengthen the fatigue and time-pressure red flag wording"
```

## Prohibited

- Declaring satisfaction after seeing only the with-skill result, without a baseline.
- Skipping the re-run just because feedback was received.
- Attaching forced assertions to a subjective skill.
- Claiming universal reliability or improvement from a passing example.

## Tool usage principle

- A viewer, grader, and benchmark are optional tools.
- The essential evidence is the actual baseline, the affected candidate decision and its checked limits.
- If a tool simplifies the loop you may use it, but if the tool itself becomes a required dependency that is excessive.

## Check sufficiency and repair limits

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.
