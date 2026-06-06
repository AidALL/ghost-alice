# Trigger Eval

This is the trigger evaluation rule used when refining a description.
## Contents

- [When to read this](#when-to-read-this)
- [Core principles](#core-principles)
- [Query set composition](#query-set-composition)
- [Judgment rules](#judgment-rules)
- [Revision principles](#revision-principles)
- [Simple result-record example](#simple-result-record-example)


## When to read this

- When the body has stabilized and you want to optimize only the description.
- When you want to judge whether the problem is undertrigger or overtrigger.
- When you want to design a should-trigger / should-not-trigger query set.

## Core principles

- The description is a trigger contract. It is not a summary of the body.
- If you only add positives, you cannot see overtrigger.
- For negatives, a near-miss matters more than an easy unrelated sentence.

## Query set composition

Base set:
- should-trigger: 8 to 10 items
- should-not-trigger: 8 to 10 items

What to include in positives:
- Cases where the user says the skill name directly.
- Cases where the same intent is expressed indirectly in different words.
- Cases where it is needed from context even without naming a file or a tool.
- Rare but genuine use cases that must be caught.

What to include in negatives:
- Cases where the keywords are similar but another skill fits better.
- Cases with the same file format but a different purpose.
- Cases that partially overlap but must not become an entry point.
- Sentences that would be wrongly triggered by a naive keyword match.

Negatives to avoid:
- Sentences completely unrelated to the skill.
- Easy examples that obviously will not trigger.

## Judgment rules

- Even if positive recall rises, it is a failure if negative false triggers increase.
- If it triggers often on near-miss negatives, the description is exaggerated.
- It is a failure if you forcibly reinforce the trigger by putting a workflow explanation into the description.

## Revision principles

- First, write the trigger context more clearly.
- Then reinforce keywords and synonyms.
- Even to the end, do not include a summary of the workflow steps.

## Simple result-record example

```yaml
description_revision: 3
positive_hits: 9/10
negative_false_triggers: 2/10
problem:
  - "Triggers excessively even on near-miss document-edit requests"
next_change:
  - "Describe the task purpose more clearly rather than the file format"
```
