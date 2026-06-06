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

Run it with a subagent and no skill. Purpose: to see exactly what fails.

What to record:
- Which choice did the agent make?
- Exactly what words did it use to justify it? (verbatim)
- Which pressure triggered the violation?

This is "seeing the test fail". You must do it before writing the skill.

### Test Scenario (GREEN)

Run the same scenario with the skill. Does the agent comply now?

If it still violates, which part of the skill is insufficient?

## 2. Pressure Types

For a rule skill, use at least three pressures in combination:

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

1. Run the pressure scenario (no skill) -> record the baseline
2. Write the skill -> respond to that specific violation
3. Run the same scenario again (with the skill) -> confirm compliance
4. Find a new violation -> add it to the table
5. Add a skill section that explicitly responds to the new violation
6. Re-test -> repeat

This is the REFACTOR stage. Continue until there are no loopholes.

### Capturing Excuses and Tabulating Them

On every test iteration:
- Record the exact words the agent used
- Analyze why that justification worked
- Add an explicit response to the skill

The excuse table helps other agents understand the skill. Each row is evidence that "in this situation you cannot rationalize this way".

## 5. Common Rationalizations and Reality

| Justification | Reality |
|--------|------|
| "No need to test because it is clear" | Clear does not equal correct. Test. |
| "Leave it as a reference" | A reference is a weird state. Same violation. |
| "I followed the spirit" | Breaking the letter equals breaking the spirit. No distinction. |
| "I will test it next time" | Next equals never. Test now. |
| "I will refactor it later" | Refactoring comes after the test passes. This is not refactoring, it is writing. |
| "I already verified it in my head" | In my head does not equal working. Written evidence is required. |
| "It is too simple" | Simple does not equal correct. The test takes 30 seconds. |
| "The senior says it is fine" | Authority cannot void the Iron Law either. Test. |

## 6. Meta Testing: Is the Test Itself Valid?

The test design also needs verification:

### Is the Pressure Sufficient?

Does the scenario reproduce real pressure? If the agent complies too easily, it means the pressure is insufficient.

-> Cut the time further. Raise the sunk cost. Add fatigue.

### Did the No-Skill Test Actually Fail?

Did the agent actually violate the rule at the baseline? If it complies even without the skill, the skill itself may not be needed.

-> Increase the pressure. Or conclude that the skill is not needed.

### Does the With-Skill Test Really Block the Excused Excuse?

After reading the skill, can the agent still use the same justification?

If yes, the skill is not explicit. It needs to be written more directly.

## 7. Test Verification Patterns

### Agreement Test vs Application Test

Bad: "Do you understand this rule?" (the agent says "yes")

Good: "Can you follow this rule in this situation?" (the agent actually follows it)

## 8. Interpreting Test Results

### Complies (GREEN)

Good. Move to the next pressure scenario. Or repeat the test (the same scenario 3 more times).

### Partially Complies

Which part of the skill is insufficient? Write that part more explicitly. Re-test.

### Does Not Comply

The skill does not respond to that specific excuse. Add it to the table. Respond explicitly with an added skill section. Re-test.

## 9. How Long Do You Keep Testing?

- Minimum: pass under 3 different pressure scenarios
- Recommended: pass under maximum pressure (all pressures at once)
- When a loophole is found: respond to that loophole and re-test

Judging "have I tested enough?": when you can no longer find a new violation. If the agent complies even after you repeat the same scenario 5 times, it is probably enough.
