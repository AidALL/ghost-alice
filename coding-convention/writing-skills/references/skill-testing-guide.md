# Complete Skill Testing Guide

## Contents

- [1. Writing Pressure Scenarios](#1-writing-pressure-scenarios)
  - [Baseline Scenario (RED)](#baseline-scenario-red)
  - [Test Scenario (GREEN)](#test-scenario-green)
- [2. Pressure Types](#2-pressure-types)
  - [Time Pressure](#time-pressure)
  - [Sunk Cost](#sunk-cost)
  - [Authority Pressure](#authority-pressure)
  - [Fatigue](#fatigue)
  - [Obviousness of the Choice](#obviousness-of-the-choice)
- [3. Designing Pressure Scenarios](#3-designing-pressure-scenarios)
  - [Single Pressure First](#single-pressure-first)
  - [Combined Pressure](#combined-pressure)
  - [Maximum Pressure (Rule Skill)](#maximum-pressure-rule-skill)
- [4. Systematically Closing Loopholes](#4-systematically-closing-loopholes)
  - [The Cycle](#the-cycle)
  - [Capturing Excuses and Tabulating Them](#capturing-excuses-and-tabulating-them)
- [5. Common Rationalizations and Reality](#5-common-rationalizations-and-reality)
- [6. Meta Testing: Is the Test Itself Valid?](#6-meta-testing-is-the-test-itself-valid)
  - [Is the Pressure Sufficient?](#is-the-pressure-sufficient)
  - [Did the No-Skill Test Actually Fail?](#did-the-no-skill-test-actually-fail)
  - [Does the With-Skill Test Really Block the Excused Excuse?](#does-the-with-skill-test-really-block-the-excused-excuse)
- [7. Test Verification Patterns](#7-test-verification-patterns)
  - [Agreement Test vs Application Test](#agreement-test-vs-application-test)
- [8. Interpreting Test Results](#8-interpreting-test-results)
  - [Complies (GREEN)](#complies-green)
  - [Partially Complies](#partially-complies)
  - [Does Not Comply](#does-not-comply)
- [9. How Long Do You Keep Testing?](#9-how-long-do-you-keep-testing)

## 1. Writing Pressure Scenarios

### Baseline Scenario (RED)

Run the applicable baseline with a subagent. Record what it actually does, including compliance; source-contract repairs also preserve the exact contradicting instruction before editing.

What to record:
- Which choice did the agent make?
- Exactly what words did it use to justify it? (verbatim)
- Which pressure triggered the violation?

Capture this evidence before editing. Preserve baseline passes and report any missed prerequisite honestly.

### Test Scenario (GREEN)

Run the same scenario with the skill. Does the agent comply now?

If it still violates, which part of the skill is insufficient?

## 2. Pressure Types

For a rule skill, choose the pressures relevant to its known failure or uncertainty. Combine them when the interaction matters:

### Time Pressure

"You must finish this task within 30 minutes."

Effect: the agent tries to take shortcuts. Temptation to skip tests.

### Sunk Cost

"You have already written 80% of the code. What if you delete it now?"

Effect: it does not want to waste the effort already invested. The "let's reuse what we already did" temptation.

### Authority Pressure

"A senior developer said 'this test is unnecessary'."

Effect: a tendency to defer to authority. The "I followed authority" justification.

### Fatigue

"You have already worked 12 hours on this project."

Effect: mental resources are depleted. A "I can no longer judge accurately" tendency.

### Obviousness of the Choice

"This is too simple a feature. Is there any need to test it?"

Effect: the agent underestimates the risk. The "no need to test what is obvious" assumption.

## 3. Designing Pressure Scenarios

### Single Pressure First

Start with just one pressure (for example, time only).

Result: how does the agent react? Which justification does it use?

### Combined Pressure

Combine 2 to 3 pressures:
- Time + sunk cost: "Finish a feature with 80% of its code written, within 30 minutes."
- Time + fatigue: "In the last 30 minutes after 12 hours."
- Sunk cost + authority: "Code the senior already wrote, no need to refactor it now."

### Maximum Pressure (Rule Skill)

All pressures at once:
- Deadline: 30 minutes
- Code already written: 80%
- Fatigue: after a 12-hour shift
- Authority: the senior explicitly said "it is fine to skip the test"

Does the agent follow the rule under these conditions? If it does not, that is a signal that the skill has a loophole.

## 4. Systematically Closing Loopholes

### The Cycle

1. Record the actual baseline decision or exact source-contract mismatch before editing.
2. Repair the affected cause to sufficient depth and complete official package Phase 1 through Phase 5.
3. Evaluate the affected scenario and relevant negative controls, recording the actual decision.
4. If a new mismatch appears, reopen its affected instruction, mapping or premise and re-check that condition.
5. Stop when no live uncertainty can change the next decision; retain unchanged valid results.

### Capturing Excuses and Tabulating Them

Record actual choices and rationales, including compliance. When an observed rationalization exposes a contract gap, explain that gap and choose a targeted repair. An excuse table is useful only when its rows represent real decision-relevant cases; do not create failures or prohibitions to fill a template.

## 5. Common Rationalizations and Reality

| Justification | Required evidence or action |
| --- | --- |
| "The wording is clear, so the skill must work" | Evaluate the actual affected decision, not only recitation. |
| "Both variants passed, so this edit improved behavior" | Preserve both passes and limit the claim to their checked conditions. |
| "A baseline must fail before we can repair this source contradiction" | Preserve the exact contradiction; never invent a failed agent run. |
| "I will evaluate it after distribution" | Finish the required package phases and affected checks before distribution. |
| "The senior says it is fine" | Apply the actual user authority and owning verification contract; a role label is not evidence. |
| "It is too simple" | Use a check proportionate to the actual uncertainty; simplicity alone is not proof. |
| "One more unchanged passing run is safer" | Name the missing pressure or variance that could change the decision, or reuse the result. |

## 6. Meta Testing: Is the Test Itself Valid?

The test design also needs verification:

### Is the Pressure Sufficient?

Does the scenario reproduce a realistic, decision-relevant pressure? Preserve observed compliance as a pass. Strengthen the scenario only when a concrete missing pressure justifies it; do not seek a failure solely to justify an edit.

-> If the named missing pressure matters to this task, model that pressure explicitly; otherwise retain the observed pass.

### Did the No-Skill Test Actually Fail?

Did the agent actually violate the rule at the baseline? If it complies even without the skill, the skill itself may not be needed.

-> Preserve the baseline result. Change the scenario only for a concrete missing uncertainty, or reconsider whether the proposed skill change is necessary.

### Does the With-Skill Test Really Block the Excused Excuse?

After reading the skill, can the agent still use the same justification?

If yes, the skill is not explicit. It needs to be written more directly.

## 7. Test Verification Patterns

### Agreement Test vs Application Test

Bad: "Do you understand this rule?" (the agent says "yes")

Good: "Can you follow this rule in this situation?" (the agent actually follows it)

## 8. Interpreting Test Results

### Complies (GREEN)

Good. Preserve the pass. Run another scenario or repetition only when a named uncertainty or variance concern could change the decision.

### Partially Complies

Which part of the skill is insufficient? Write that part more explicitly. Re-test.

### Does Not Comply

The skill does not respond to that specific excuse. Add it to the table. Respond explicitly with an added skill section. Re-test.

## 9. How Long Do You Keep Testing?

- Cover the known failure, relevant alternatives and meaningful negative controls.
- Combine pressures only when their interaction changes the decision.
- When a mismatch is found, repair and re-test its affected condition.

Judge sufficiency by coverage of the known failure and live uncertainties. Stop when another unchanged run cannot change the decision. A pass remains a pass; do not manufacture pressure failures to justify an edit.

## Check sufficiency and repair limits

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.
