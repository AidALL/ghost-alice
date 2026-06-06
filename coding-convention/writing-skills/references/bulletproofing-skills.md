# Bulletproofing Rule Skills Against Rationalizations

Rule-enforcing skills (TDD, verification before completion, design before coding, and so on) need to hold up against rationalizations. Agents are smart and will look for loopholes under pressure.

Psychology background: understanding why persuasion techniques work lets you apply countermeasures systematically. Research on the principles of authority, commitment, scarcity, social proof, and unity (Cialdini, 2021; Meincke et al., 2025).
## Contents

- [1. Close Every Loophole Explicitly](#1-close-every-loophole-explicitly)
- [2. Counter the "Spirit vs Letter" Argument](#2-counter-the-spirit-vs-letter-argument)
- [3. Write a Rationalization Table](#3-write-a-rationalization-table)
- [4. Build a Red Flags List](#4-build-a-red-flags-list)
- [5. Update Violation Symptoms in the Description](#5-update-violation-symptoms-in-the-description)
- [Anti-patterns](#anti-patterns)
  - [❌ Narrative example](#narrative-example)
  - [❌ Multi-language dilution](#multi-language-dilution)
  - [❌ Code in a flowchart](#code-in-a-flowchart)
  - [❌ Generic labels](#generic-labels)
- [Capture Rationalization Patterns in Tests](#capture-rationalization-patterns-in-tests)


## 1. Close Every Loophole Explicitly

Do not just state the rule. Forbid the specific workarounds:

Bad example:

```markdown
Code without writing the test first? Delete it.
```

Good example:

```markdown
Code without writing the test first? Delete it. Start over.

No exceptions:
- Do not keep it for reference
- Do not "adapt" it while writing the test
- Do not just look at it
- Delete means delete
```

## 2. Counter the "Spirit vs Letter" Argument

Add a foundational principle up front:

```markdown
Breaking the letter of the rule is breaking the spirit of the rule.
```

This blocks the entire class of "I followed the spirit" rationalizations.

## 3. Write a Rationalization Table

Put every rationalization that surfaced in the baseline tests into a table:

| Rationalization | Reality |
|------|------|
| "Too simple to need a test" | Simple code breaks too. A test takes 30 seconds. |
| "I will test it later" | A passing test proves nothing on its own. |
| "Writing the test after reaches the same goal" | Test first = "what do I need to do?" / Test after = "what does this even do?" |

## 4. Build a Red Flags List

So the agent can self-check when it starts rationalizing:

```markdown
Red Flags: stop and start over

- Writing code without a test
- "I already tested it manually"
- "Writing the test after is the same goal"
- "The spirit matters, not the form"
- "This one is different because..."

All of these mean: delete the code. Back to TDD.
```

## 5. Update Violation Symptoms in the Description

Add the signal symptoms of an attempt to break the rule into the description:

```yaml
description: Use before implementing a feature or a bug fix. Stop if you are already implementing or have already written the code.
```

## Anti-patterns

### ❌ Narrative example

"In the 2025-10-03 session we had an empty projectDir that..."

Why it is bad: too specific, not reusable

### ❌ Multi-language dilution

example-js.js, example-py.py, example-go.go

Why it is bad: mediocre quality, maintenance burden

### ❌ Code in a flowchart

```dot
step1 [label="import fs"];
step2 [label="read file"];
```

Why it is bad: cannot copy-paste, hard to read

### ❌ Generic labels

helper1, helper2, step3, pattern4

Why it is bad: a label must carry meaning.

## Capture Rationalization Patterns in Tests

When you run the baseline tests, record the agent's exact wording:

Instead of asking "why are you skipping the test?", listen to what the agent says naturally:
- "Because this is clear"
- "I will test it later"
- "I already checked it in my head"
- "Because I followed the spirit"

Put each rationalization into the table exactly as said. This becomes the rationale for the skill.
