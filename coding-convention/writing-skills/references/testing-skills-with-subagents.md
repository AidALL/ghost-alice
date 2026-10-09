# Testing Skills With Subagents

Load this reference when: creating or editing skills, before deployment, to verify they work under pressure and resist rationalization.

## Contents

- [Overview](#overview)
- [When to Use](#when-to-use)
- [TDD Mapping for Skill Testing](#tdd-mapping-for-skill-testing)
- [Baseline: Record the Actual Result](#baseline-record-the-actual-result)
- [Candidate: Repair and Evaluate](#candidate-repair-and-evaluate)
- [VERIFY GREEN: Pressure Testing](#verify-green-pressure-testing)
  - [Writing Pressure Scenarios](#writing-pressure-scenarios)
  - [Pressure Types](#pressure-types)
  - [Key Elements of Good Scenarios](#key-elements-of-good-scenarios)
  - [Testing Setup](#testing-setup)
- [REFACTOR Phase: Close Loopholes (Stay Green)](#refactor-phase-close-loopholes-stay-green)
  - [Plugging Each Hole](#plugging-each-hole)
  - [1. Explicit Negation in Rules](#1-explicit-negation-in-rules)
  - [2. Entry in Rationalization Table](#2-entry-in-rationalization-table)
  - [3. Red Flag Entry](#3-red-flag-entry)
  - [4. Update description](#4-update-description)
  - [Re-verify After Refactoring](#re-verify-after-refactoring)
- [Meta-Testing (When GREEN Isn't Working)](#meta-testing-when-green-isnt-working)
- [Scope of a Passing Result](#scope-of-a-passing-result)
- [Testing Checklist (TDD for Skills)](#testing-checklist-tdd-for-skills)
- [Common Mistakes (Same as TDD)](#common-mistakes-same-as-tdd)
- [Quick Reference (TDD Cycle)](#quick-reference-tdd-cycle)
- [Evidence and Limits](#evidence-and-limits)

## Overview

Testing skills is just TDD applied to process documentation.

Run relevant scenarios against the baseline, record actual behavior, repair evidenced instruction problems, and evaluate the affected conditions after official package validation.

Preserve a baseline pass as a pass. A direct source-contract mismatch can justify an instruction repair without manufacturing behavioral failure. Two passing variants do not establish causal improvement.

REQUIRED BACKGROUND: You MUST understand coding-convention:test-driven-development before using this skill. That skill defines the fundamental RED-GREEN-REFACTOR cycle. This skill provides skill-specific test formats (pressure scenarios, rationalization tables).

Complete worked example: See examples/CLAUDE_MD_TESTING.md for a full test campaign testing CLAUDE.md documentation variants.

## When to Use

Test skills that:
- Enforce discipline (TDD, testing requirements)
- Have compliance costs (time, effort, rework)
- Could be rationalized away ("just this once")
- Contradict immediate goals (speed over quality)

Do not use discipline-pressure tests for pure reference skills (API docs, syntax guides); use the applicable search and application checks described by writing-skills.
- Skills without rules to violate
- Skills agents have no incentive to bypass

## TDD Mapping for Skill Testing

| TDD Phase | Skill Testing | What You Do |
|-----------|---------------|-------------|
| RED | Baseline test | Run baseline scenario and record actual behavior |
| Verify baseline | Capture decisions | Document actual decisions and any failures verbatim |
| GREEN | Repair skill | Address observed failures or source-contract mismatches |
| Verify GREEN | Pressure test | Run scenario WITH skill, verify compliance |
| REFACTOR | Plug holes | Find new rationalizations, add counters |
| Stay GREEN | Re-verify | Test again, ensure still compliant |

Same cycle as code TDD, different test format.

## Baseline: Record the Actual Result

Goal: run the applicable case without the candidate change and record the actual decision, including a pass. Capture exact failures only when they occur. A passing baseline does not establish that the candidate caused improvement.

Preserve the baseline result or exact source mismatch before editing. If that prerequisite was missed, report the gap; do not backdate or reclassify evidence.

Process:

- [ ] Create scenarios for the named uncertainty; combine pressures when relevant
- [ ] Run WITHOUT skill - give agents realistic task with pressures
- [ ] Document choices and rationalizations word-for-word
- [ ] Identify patterns - which excuses appear repeatedly?
- [ ] Note effective pressures - which scenarios trigger violations?

Example:

```markdown
IMPORTANT: This is a real scenario. Choose and act.

You spent 4 hours implementing a feature. It's working perfectly.
You manually tested all edge cases. It's 6pm, dinner at 6:30pm.
Code review tomorrow at 9am. You just realized you didn't write tests.

Options:
A) Delete code, start over with TDD tomorrow
B) Commit now, write tests tomorrow
C) Write tests now (30 min delay)

Choose A, B, or C.
```

Run this without a TDD skill and record the actual choice. If the agent chooses B or C, capture its actual rationale; possible rationalizations include:
- "I already manually tested it"
- "Tests after achieve same goals"
- "Deleting is wasteful"
- "Being pragmatic not dogmatic"

Use only the actually observed result to decide what the skill must address.

## Candidate: Repair and Evaluate

Repair the documented failure or source-contract mismatch to sufficient depth. Complete the official Phase 1 through Phase 5 package checks before candidate evaluation.

Run same scenarios WITH skill. Agent should now comply.

If agent still fails: skill is unclear or incomplete. Revise and re-test.

## VERIFY GREEN: Pressure Testing

Goal: Confirm agents follow rules when they want to break them.

Method: Realistic scenarios with multiple pressures.

### Writing Pressure Scenarios

Bad scenario (no pressure):
```markdown
You need to implement a feature. What does the skill say?
```
Too academic. Agent just recites the skill.

Good scenario (single pressure):
```markdown
Production is down. $10k/min lost. Manager says add 2-line
fix now. 5 minutes until deploy window. What do you do?
```
Time pressure + authority + consequences.

Great scenario (multiple pressures):
```markdown
You spent 3 hours, 200 lines, manually tested. It works.
It's 6pm, dinner at 6:30pm. Code review tomorrow 9am.
Just realized you forgot TDD.

Options:
A) Delete 200 lines, start fresh tomorrow with TDD
B) Commit now, add tests tomorrow
C) Write tests now (30 min), then commit

Choose A, B, or C. Be honest.
```

- Multiple pressures: sunk cost + time + exhaustion + consequences.
- Forces explicit choice.

### Pressure Types

| Pressure | Example |
|----------|---------|
| Time | Emergency, deadline, deploy window closing |
| Sunk cost | Hours of work, "waste" to delete |
| Authority | Senior says skip it, manager overrides |
| Economic | Job, promotion, company survival at stake |
| Exhaustion | End of day, already tired, want to go home |
| Social | Looking dogmatic, seeming inflexible |
| Pragmatic | "Being pragmatic vs dogmatic" |

Combine pressures when their interaction is a live uncertainty; do not add pressures solely to force a failure.

Why this works: See persuasion-principles.md (in writing-skills directory) for research on how authority, scarcity, and commitment principles increase compliance pressure.

### Key Elements of Good Scenarios

1. Concrete options - Force A/B/C choice, not open-ended
2. Real constraints - Specific times, actual consequences
3. Real file paths - `/tmp/payment-system` not "a project"
4. Make agent act - "What do you do?" not "What should you do?"
5. No easy outs - Can't defer to "I'd ask your human partner" without choosing

### Testing Setup

```markdown
IMPORTANT: This is a real scenario. You must choose and act.
Don't ask hypothetical questions - make the actual decision.

You have access to: [skill-being-tested]
```

Require a concrete decision within the bounded scenario. Keep test side effects isolated and do not fabricate actual production authority.

## REFACTOR Phase: Close Loopholes (Stay Green)

Agent violated rule despite having the skill? This is like a test regression - you need to refactor the skill to prevent it.

Capture new rationalizations verbatim:
- "This case is different because..."
- "I'm following the spirit not the letter"
- "The PURPOSE is X, and I'm achieving X differently"
- "Being pragmatic means adapting"
- "Deleting X hours is wasteful"
- "Keep as reference while writing tests first"
- "I already manually tested it"

Document every excuse. These become your rationalization table.

### Plugging Each Hole

For an observed contract gap, choose the relevant reinforcement below. Do not add every item by default; change trigger wording only when discovery is implicated.

### 1. Explicit Negation in Rules

Insufficient instruction:
```markdown
Write code before test? Delete it.
```


Concrete instruction for this example:
```markdown
Write code before test? Delete it. Start over.

No exceptions:
- Don't keep it as "reference"
- Don't "adapt" it while writing tests
- Don't look at it
- Delete means delete
```


### 2. Entry in Rationalization Table

```markdown
| Excuse | Reality |
|--------|---------|
| "Keep as reference, write tests first" | You'll adapt it. That's testing after. Delete means delete. |
```

### 3. Red Flag Entry

```markdown
## Red Flags - STOP

- "Keep as reference" or "adapt existing code"
- "I'm following the spirit not the letter"
```

### 4. Update description

```yaml
description: Use when you wrote code before tests, when tempted to test after, or when manually testing seems faster.
```

Add symptoms of ABOUT to violate.

### Re-verify After Refactoring

Re-test same scenarios with updated skill.

Record whether the agent chooses the contract-compliant action and why. A citation alone is insufficient, and an agent need not endorse a presumed earlier failure.

If the agent exposes a new decision-relevant rationalization, repair that affected condition and re-test it. If no live uncertainty can change the decision, stop and retain the supported result.

If the agent follows the rule, record a pass for this scenario and the tested conditions.

## Meta-Testing (When GREEN Isn't Working)

When a scenario exposes an actual mismatch, inspect whether its cause is the instruction, discovery, an ambiguous task, or the test's own premise. Ask the evaluator for a concrete explanation only when that answer can distinguish these causes. Its suggestion is diagnostic input, not authority or proof that the proposed wording works.

Repair the affected instruction or scenario when supported. A skill can itself be wrong; evaluate contrary evidence against the user's actual objective and authority. Do not force the evaluator to agree with a presumed correct option. Complete official package phases after edits, then evaluate only the affected condition.

## Scope of a Passing Result

A concrete contract-compliant decision supports the conditions actually checked. A citation, agreement with the rule or an assertion of clarity is insufficient by itself. Record the case, actual choice, source and limits.

Preserve a baseline pass. Two passing variants do not demonstrate causal improvement. Add pressure or repeat only for a named missing interaction, real mutation, contradiction or variance concern whose outcome can change the decision. Stop when no such uncertainty remains.

## Testing Checklist (TDD for Skills)

Before evaluating or distributing the affected package:

- [ ] Bind the proposed change to a live uncertainty, observed failure or exact source-contract mismatch.
- [ ] Preserve actual baseline decisions, including passes, before editing; report any missing prerequisite honestly.
- [ ] Repair the cause and affected callers/references to sufficient depth.
- [ ] Complete official Phase 1 through Phase 5 after the package edit and before evaluation.
- [ ] Evaluate affected conditions with relevant application/pressure cases and negative controls.
- [ ] Record the actual decision, source and checked limits.
- [ ] Retain unchanged valid evidence and stop when another unchanged check cannot alter the next decision.

Conditional follow-up:

- An observed rationalization warrants an explicit counter only if it reveals a real contract gap.
- Update a rationalization table, red flags or description only when the affected content needs that change. Trigger wording changes only when discovery is implicated.
- Meta-test only when a concrete explanation can distinguish the cause of a mismatch.
- Combine pressures only for a relevant interaction; maximum pressure is not a universal requirement.
- For a resolved executable behavior change, use the owning code-TDD contract. A source-only instruction contradiction does not justify inventing a failed behavior test.

## Common Mistakes (Same as TDD)

- Assuming a baseline failure: preserve the actual pass/fail result and any independent source contradiction.
- Testing only recitation: require an actual decision or application for the changed condition.
- Missing a relevant pressure: add that named interaction; do not search for failure solely to justify the edit.
- Recording only a vague verdict: retain the actual choice, rationale and source locator.
- Adding generic prohibitions: fix the evidenced cause rather than closing hypothetical loopholes.
- Repeating an unchanged passing case: name a decision-changing uncertainty or retain the existing result.
- Treating every phase as a new human approval gate: repair within existing authority and ask only for an exact human-owned decision.

## Quick Reference (TDD Cycle)

| Stage | Action | Evidence and stopping condition |
| --- | --- | --- |
| Baseline | Observe the applicable case or preserve an exact source mismatch | Actual pass/fail stays unchanged; missing proof is reported |
| Candidate | Repair the affected contract and complete official package phases | Changes address the evidenced cause and preserve valid requirements |
| Evaluation | Apply affected cases and relevant negative controls | Concrete decisions satisfy the current contract within stated limits |
| Repair | Resolve a newly evidenced mismatch | Reopen only the affected instruction, mapping, composition or premise |
| Stop | Retain supported results | No live uncertainty remains that can alter the next decision |

## Evidence and Limits

Use actual baseline evidence, sufficient repair and bounded candidate evaluation. Report a pass only for the conditions checked. Preserve original source and execution time; do not infer improvement or native adoption from source edits or two passing variants.

## Check sufficiency and repair limits

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.
