---
name: systematic-debugging
description: Use when facing bugs, test failures, or unexpected behavior before proposing fixes. Enforces root-cause investigation, pattern analysis, hypothesis/verification, and correction. Symptom-only fixes are failures.
compatibility:
  - "Python 3.11+ standard library"
---

# Systematic Debugging
## Contents

- [Overview](#overview)
- [Iron Law](#iron-law)
- [Where It Applies](#where-it-applies)
- [The 4 Phases](#the-4-phases)
  - [Phase 1: Root-Cause Investigation](#phase-1-root-cause-investigation)
  - [Phase 2: Pattern Analysis](#phase-2-pattern-analysis)
  - [Phase 3: Hypothesis and Test](#phase-3-hypothesis-and-test)
  - [Phase 4: Implementation](#phase-4-implementation)
- [Red Flags. Stop and Return to the Procedure.](#red-flags-stop-and-return-to-the-procedure)
- [Signs the User Thinks You Are Going Wrong](#signs-the-user-thinks-you-are-going-wrong)
- [Common Rationalizations](#common-rationalizations)
- [Quick Reference](#quick-reference)
- [When the Procedure Reveals "No Root Cause"](#when-the-procedure-reveals-no-root-cause)
- [Supporting Techniques](#supporting-techniques)
- [Repair limits, authority and useful checks](#repair-limits-authority-and-useful-checks)


## Overview

Random fixes waste time and create new bugs. A quick patch hides the root problem.

○ Core principles

- Always find the root cause before fixing. A symptom-only fix is a failure.
- Breaking the letter of this procedure is breaking the spirit of debugging.

## Iron Law

```
Do not attempt a fix before finishing the root-cause investigation.
```

If you have not finished Phase 1, you cannot propose a fix.

## Where It Applies

Apply it to every technical issue.

- Test failures
- Production bugs
- Unexpected behavior
- Performance problems
- Build failures
- Integration issues

□ Situations where it especially must be applied

- Under time pressure (the more urgent the situation, the stronger the temptation to guess)
- When it looks obvious, like "just fix it quickly this once"
- After you have already tried several fixes
- When a previous fix is not working
- When you do not fully understand the issue

□ Situations you must not skip

- The issue looks simple (even a simple bug has a root cause)
- It is urgent (rushing guarantees rework)
- A manager says to fix it right now (being systematic is faster than thrashing)

## The 4 Phases

Do not move to the next phase before finishing each phase.

### Phase 1: Root-Cause Investigation

Do this before attempting a fix.

□ 1. Read the error message carefully

- Do not skip the error or warning
- It often contains the exact solution
- Read the stack trace to the end
- Record the line number, file path, and error code

□ 2. Reproduce it consistently

- Can you trigger it reliably?
- What are the exact steps?
- Does it happen every time?
- If it does not reproduce, gather more data. Do not guess.

□ 3. Check recent changes

- What changed that could cause this?
- git diff, recent commits
- New dependencies, configuration changes
- Environment differences

□ 3-A. Assign cause weights

- Keep all candidate causes open, but verify the largest recent-change cause first.
- Finding the largest cause does not mean closing it as the only cause.
- After the first fix, re-check residual causes, downstream impact, and secondary symptoms.
- Record it as "I looked at this first and here is the remaining impact", not "that was the only problem".

□ 4. Collect evidence in a multi-component system

When the system has multiple components (CI -> build -> signing, API -> service -> database):

Add diagnostic instrumentation before proposing a fix.

```
At each component boundary:
  - Log the data coming into the component
  - Log the data going out of the component
  - Verify environment and configuration propagation
  - Check the state of each layer

Run it once to gather evidence that shows where it breaks
Then analyze the evidence to identify the failing component
Then investigate that specific component
```

Multi-layer system example:

```bash
# Layer 1: workflow
echo "=== secrets available in the workflow: ==="
echo "IDENTITY: ${IDENTITY:+SET}${IDENTITY:-UNSET}"

# Layer 2: build script
echo "=== environment variables in the build script: ==="
env | grep IDENTITY || echo "IDENTITY not in environment"

# Layer 3: signing script
echo "=== keychain state: ==="
security list-keychains
security find-identity -v

# Layer 4: actual signing
codesign --sign "$IDENTITY" --verbose=4 "$APP"
```

What this reveals: which layer fails (secrets -> workflow ✓, workflow -> build ✗)

□ 5. Trace the data flow

When the error is deep in the call stack:

The complete backward-tracing technique is in `references/root-cause-tracing.md` in the same directory.

Quick version:

- Where does the wrong value start?
- Who called this with the wrong value?
- Keep tracing upward until you find the source
- Fix the source, not the symptom

### Phase 2: Pattern Analysis

Find the pattern before fixing.

□ 1. Find a working example

- Find code in the same codebase that behaves similarly
- What is similar to the broken thing but works?

□ 2. Compare against the reference

- If you are implementing a pattern, read the reference implementation to the end
- Do not skim. Read every line
- Fully understand the pattern before applying it

□ 3. Identify the differences

- What is the difference between the working thing and the broken thing?
- List every difference, no matter how small
- Do not assume "that cannot matter"

□ 4. Understand the dependencies

- What other components does this need?
- What settings, configuration, or environment?
- What assumptions is it making?

### Phase 3: Hypothesis and Test

The scientific method.

□ 1. Form a single hypothesis

- State it clearly: "I think X is the root cause because Y"
- Write it down
- Be specific, not vague

□ 2. Minimal test

- The smallest change that verifies the hypothesis
- One variable at a time
- Do not fix several things at once

□ 3. Verify before continuing

- Did it work? Yes -> Phase 4
- Did it not work? Form a new hypothesis
- Do not stack more fixes on top

□ 4. When you do not know

- Say "I do not understand X"
- Do not pretend to know
- Ask for help
- Investigate more

### Phase 4: Implementation

Fix the root cause, not the symptom.

□ 1. Write a failing test case

- The simplest possible reproduction
- An automated test if possible
- A one-off test script if there is no framework
- It must exist before the fix
- Use the `test-driven-development` skill to write a proper failing test

□ 2. Implement a single fix

- Address the identified root cause
- Make one coherent, evidence-backed repair at sufficient depth
- No unrelated "while I am here" improvements
- Include structural changes when the identified cause requires them

□ 3. Verify the fix

- Does the test pass now?
- Did any other test break?
- Is the issue actually resolved?

□ 4. When the fix does not work

Stop repeating an unsupported fix. Record the new result and identify which hypothesis, source-to-target mapping or architectural premise it challenges. Return directly to that affected cause; retain valid observations and unrelated checks.

Repeated failures increase the reason to inspect shared state, coupling and the overall design. A count alone does not prove that the architecture is wrong, and it does not prohibit an evidence-backed repair within existing authority.

□ 5. Reopen the implicated architectural premise

Signals worth investigating include fixes that expose problems in different places, shared-state coupling, or a correction whose necessary scope is structural. Compare the current design against requirements and contrary evidence. Resolve whether the cause is local, a wrong hypothesis, a mapping problem or an architectural mismatch.

Repair the supported cause to sufficient depth under the accepted objective and authority. For a resolved executable change, follow the owning code-TDD workflow and verify the affected behavior. Ask the user only for an unresolved human-owned decision or changed explicit boundary. Continue independent authorized work; stop unchanged investigation when no outcome can change the next decision.

## Red Flags. Stop and Return to the Procedure.

If the following thoughts come up:

- "Fix it fast now and investigate later"
- "Just change X and see if it works"
- "Add several changes and run the test"
- "Skip the test and verify manually"
- "It is probably X, so let us fix that"
- "I do not fully understand it but this might work"
- "The pattern is X but let us adapt it differently"
- "The main problems are as follows: [a list of fixes with no investigation]"
- Proposing a solution before tracing the data flow
- "Just one more fix" without a changed hypothesis or new evidence
- Each fix reveals a new problem in a different place

Stop the unsupported action and reopen the affected investigation. Reuse valid evidence; repeated failures are a signal to assess the architectural premise in Phase 4.5, not a diagnosis by count.

## Signs the User Thinks You Are Going Wrong

Watch for the following turns of phrase.

- "Is that not happening?" is You assumed without verifying.
- "Want me to show you?" is You should have added evidence collection.
- "Stop guessing" is You are proposing a fix without understanding.
- "Do some ultrathinking" is Suspect the foundation, not the symptom.
- "Are you stuck?" in a frustrated tone means the approach is not working.

When you see these signs: stop. Return to Phase 1.

## Common Rationalizations

| Excuse | Reality |
|------|------|
| "The issue is simple, no procedure needed" | Even a simple issue has a root cause. The procedure is fast on a simple bug. |
| "Emergency, no time for the procedure" | Systematic debugging is faster than guess-and-check thrashing. |
| "Let me try this first and investigate" | The first fix sets the pattern. Do it right from the start. |
| "Write the test after confirming the fix works" | An unverified fix does not stick. The test proves it first. |
| "Several fixes at once saves time" | You cannot isolate what worked. It creates new bugs. |
| "The reference is too long, just adapt the pattern" | Partial understanding guarantees bugs. Read it to the end. |
| "I see the problem, I should fix it" | Seeing the symptom != understanding the root cause |
| "Just one more fix" without new evidence | Reopen the implicated hypothesis or architectural premise. Failure count alone proves no cause; continue an evidence-backed in-scope repair and its affected check. |

## Quick Reference

| Phase | Core activity | Success criterion |
|------|----------|----------|
| 1. Root cause | Read the error, reproduce, check changes, gather evidence | Understand the what and the why |
| 2. Pattern | Find a working example, compare | Identify the differences |
| 3. Hypothesis | Form a theory, run a minimal test | Confirmation or a new hypothesis |
| 4. Implementation | Write a test, fix, verify | Bug resolved, test passes |

## When the Procedure Reveals "No Root Cause"

When systematic investigation reveals that the issue is genuinely environmental, timing-dependent, or external:

- You have completed the procedure
- Document what you investigated
- Implement appropriate handling (retry, timeout, error message)
- Add monitoring and logging for future investigation

But many "no root cause" cases are incomplete investigation.

## Supporting Techniques

`references/` in the same directory contains supporting techniques that are part of systematic debugging.

- `root-cause-tracing.md`: trace the call stack backward to find the original trigger.
- `defense-in-depth.md`: add validation at multiple layers after finding the root cause.
- `condition-based-waiting.md`: replace arbitrary timeouts with condition polling.

Related skills

- `test-driven-development`: for writing a failing test case (Phase 4, Step 1).
- `verification-before-completion`: verify the fix before claiming success.

## Repair limits, authority and useful checks

Actual current and prior human instructions supply the authorization envelope: objective, prohibitions, allowed actions and targets, side effects and resources. Retained authority facts preserve their human source; trusted system/project constraints retain their identified source. Intake compresses semantic facts and the router selects current work without granting authority. Distinguish human-owned criteria and resource choices from agent-derived interpretations and bounded local plans; an absent explicit resource cap is not an approved human limit. Renew a narrower implementation contract only inside actual authority, without weakening acceptance criteria or inferring permission from focus expansion. Request an exact human-owned decision before crossing its boundary. Actual grant renews only the affected plan and granted scope; denial parks it; no response leaves it waiting. Continue other eligible authorized work.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.

The owning workflow calls the selected business action or corrective change and receives its actual result; declared action output may itself supply the verification observation. Domain verification returns actual observations, source and time to the workflow for criterion judgment. The applicable registered v2 pretool automatically prepares AP origin/receipt before the tool and returns current qualified command context. Reuse that valid prepared result; use an explicit returned preparation helper only when the applicable dependency remains genuinely unfulfilled. A recording gap affects capture/publication/application recordability; AP pretool continues and native tool permission remains with its host. Required or discriminating business observation follows its own authority and resource bounds; recording recovery rules govern recording attempts only. Preserve valid proof and its original source/time, including when recording is unavailable.

For a candidate closure, the owning workflow obtains or reuses supported proof. Applicable AP publication uses the exact current qualified selection, prepared AP receipt and actual returned publish command to leave a pending-adapter request. Native candidate/final dispatch invokes its actually registered handlers: the completion guard checks candidate fields and applicable skill evidence; the privileged AP Stop adapter revalidates publication, proof, ownership and dependencies. Only accepted current publication reaches the exact Core criterion writer within the shared authority transaction. Missing, stale or invalid publication returns a recording diagnostic without a criterion write. Actual application status and remaining criteria determine unit recording/completion claims. The handlers retain their actual native invocation semantics. Core owns delivery policy and may assemble the permitted payload; the workflow/native host performs visible delivery. Candidate claims use the supported current scope, while actual recording/application and visibility results govern further repair and closure claims. Preserve already-visible unchanged business content for applicable control-only repair. Return an admissible affected step to the workflow, or honest partial/waiting status when no eligible continuation remains.
