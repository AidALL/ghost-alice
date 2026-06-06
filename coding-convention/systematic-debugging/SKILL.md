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
- One change at a time
- No "while I am here" improvements
- No bundled refactoring

□ 3. Verify the fix

- Does the test pass now?
- Did any other test break?
- Is the issue actually resolved?

□ 4. When the fix does not work

- Stop
- Count: how many fixes have you tried?
- Fewer than 3: go back to Phase 1 and re-analyze with the new information
- 3 or more: stop and suspect the architecture (number 5 below)
- Do not try a fourth fix without an architecture discussion

□ 5. When 3 or more fixes have failed: suspect the architecture

Patterns that suggest an architecture problem:

- Each fix reveals new shared state, coupling, or problems in a different place
- The fix requires a "large refactoring"
- Each fix creates a new symptom somewhere else

Stop and suspect the foundation.

- Is this pattern fundamentally sound?
- Are you "just holding onto it out of inertia"?
- Keep fixing symptoms vs. refactor the architecture, which one?

Discuss with the user before trying more fixes.

This is not a hypothesis failure, it is a wrong architecture.

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
- "Just one more fix" (after already trying 2 or more)
- Each fix reveals a new problem in a different place

They all mean one thing. Stop. Return to Phase 1.

When 3 or more fixes have failed: suspect the architecture (see Phase 4.5)

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
| "Just one more fix" (after 2 failures) | 3 or more failures = an architecture problem. Suspect the pattern, do not fix it again. |

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
