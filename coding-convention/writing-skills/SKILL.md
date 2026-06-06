---
name: writing-skills
description: Use when writing a new skill or modifying/verifying an existing skill. Confirms the skill works under pressure scenarios before distribution.
compatibility:
  - "Python 3.11+ standard library"
---

# Writing Skills
## Contents

- [Overview](#overview)
- [What Is a Skill?](#what-is-a-skill)
- [Applying TDD to Writing Skills](#applying-tdd-to-writing-skills)
- [Closed-Loop Checkpoints](#closed-loop-checkpoints)
- [When to Write a Skill](#when-to-write-a-skill)
- [Skill Types](#skill-types)
- [Directory Structure](#directory-structure)
- [SKILL.md Structure](#skillmd-structure)
- [Claude Search Optimization](#claude-search-optimization)
  - [The Role of the Description Field](#the-role-of-the-description-field)
  - [Keyword Coverage](#keyword-coverage)
  - [Token Efficiency](#token-efficiency)
  - [Referencing Other Skills](#referencing-other-skills)
- [The Iron Law (Same as TDD)](#the-iron-law-same-as-tdd)
- [Test Strategy](#test-strategy)
- [Red Flags. Signs to Stop and Start Over.](#red-flags-signs-to-stop-and-start-over)
- [The RED-GREEN-REFACTOR Cycle](#the-red-green-refactor-cycle)
- [Skill-Writing Checklist (TDD Adapted)](#skill-writing-checklist-tdd-adapted)
- [Using Flowcharts](#using-flowcharts)
- [Code Examples](#code-examples)
- [STOP: Before Moving to the Next Skill](#stop-before-moving-to-the-next-skill)
- [Discovery Workflow](#discovery-workflow)
- [Summary](#summary)
- [Reference Documents](#reference-documents)


## Overview

Writing a skill applies TDD (test-driven development) to process documentation. Write the test cases first (pressure scenarios), observe the failure (the baseline), write the skill document (the implementation), and confirm success again (verification). Finally, close the loopholes (refactoring).

Core principle: if you have not directly seen an agent fail without the skill, you cannot know whether the skill teaches the right thing.

Required background: you must first understand coding-convention:test-driven-development. That skill defines the foundation of the RED-GREEN-REFACTOR cycle.

## What Is a Skill?

A skill is a reference guide to a proven technique, pattern, or tool. It helps future Claude instances find and apply an effective approach.

A skill is a reusable technique, pattern, and tool. It is not a narrative of one time you solved a problem.

## Applying TDD to Writing Skills

| TDD concept | Writing skills |
|---------|--------|
| Test case | A pressure scenario including subagents |
| Production code | The skill document (SKILL.md) |
| RED failure | The agent violates the rule without the skill |
| GREEN success | The agent complies when the skill is present |
| REFACTOR | Maintain compliance while closing loopholes |

The entire skill-writing process follows RED-GREEN-REFACTOR.

## Closed-Loop Checkpoints

- Right after RED: check whether the baseline failure and the pressure scenario actually target the same problem.
- Right after GREEN: check whether the current draft's `description`, examples, references, and calls directly prevent the failure revealed in RED.
- Right after REFACTOR: reconfirm with the same scenarios that no new loophole, no new misfire, and no new hidden dependency was introduced.
- When a mismatch appears, do not add explanation. Rewind to the previous step and fix it.

## When to Write a Skill

Write a skill when:
- The technique is not intuitive.
- It is referenced repeatedly across multiple projects.
- The pattern applies broadly (not per project).
- Others would benefit too.

Do not write a skill when:
- It is a one-off solution.
- The standard is already well documented elsewhere.
- It is a project-specific rule (describe it in CLAUDE.md).
- The constraint can be enforced by a regular expression or automation.

## Skill Types

Technique: a concrete method with steps to follow (for example, condition-based waiting, root-cause tracing).

Pattern: a way of thinking about a problem (for example, flattening with flags, invariant testing).

Reference: API documentation, syntax guides, tool manuals.

## Directory Structure

```
skills/
  skill-name/
    SKILL.md              # required
    supporting-file.*     # only when needed
```

Flat namespace. Every skill is searchable.

Split out:
- Large references (100+ lines): API docs, comprehensive syntax.
- Reusable tools: scripts, utilities, templates.

Keep inline:
- Principles and concepts.
- Code patterns (< 50 lines).
- Everything else.

## SKILL.md Structure

Frontmatter (YAML):
- Required fields: `name`, `description`.
- `name`: lowercase, digits, and hyphens only (no special characters).
- `description`: third person, start with "Use when...", trigger conditions only (no process description).

Body structure:
- Overview: what is this? Core principle in 1-2 sentences.
- When to Use: symptoms and use cases (a short flowchart if the decision is not self-evident).
- Core Pattern (for technique/pattern skills): a Before/After code comparison.
- Quick Reference: a table or bullet list for skimming.
- Implementation: inline when simple, a file link when long.
- Common Mistakes: failures and solutions.

## Claude Search Optimization

Making sure future Claude can find the skill is important.

### The Role of the Description Field

The description must do only two things:
1. State the trigger conditions specifically.
2. No process or workflow description.

Test result: when the description summarizes the workflow, Claude follows the description without reading the body. A description like "proceeds between code reviews" makes Claude do only one review, even though two are actually needed.

When you change the description to "use when executing an implementation plan as an independent task", Claude reads the body and correctly follows the two reviews.

Good examples:
- "Use when executing an implementation plan as an independent task in the current session"
- "Use before implementing a feature or a bug fix"

Bad examples:
- "Use TDD: test first, see the failure, minimal code, refactor" (a workflow summary)
- "For asynchronous testing" (too abstract)

### Keyword Coverage

Use the words Claude would search for:
- Error messages: "timeout exceeded", "ENOTEMPTY"
- Symptoms: "flaky", "hanging", "race condition"
- Synonyms: "cleanup/teardown", "pollution"
- Tool names: actual commands, library names

### Token Efficiency

A frequently loaded skill is included in every conversation. Tokens are precious.

Target word counts:
- getting-started workflows: < 150 words
- frequently loaded skills: < 200 words
- everything else: < 500 words

Techniques: move detail into tool help, reference other skills, compress examples, remove duplication.

### Referencing Other Skills

```markdown
Required prerequisite: you must understand coding-convention:test-driven-development
```

Do not use `@` links. They load the file immediately and waste context.

## The Iron Law (Same as TDD)

```
No writing a skill without a failing test
```

This applies both to new skills and to modifying existing skills.

Wrote the skill first and tested after? Delete it. Start over.
Modified the skill but did not test it? Same violation.

No exceptions:
- Not even a simple addition.
- Not even adding a section.
- Not even a documentation update.
- Do not keep an untested change as a reference.
- Do not "adapt" while running the test.

## Test Strategy

Test approach by skill type:

Rule-enforcement skills (TDD, verification-before-completion):
- Academic question: does it understand the rule?
- Pressure scenario: does it comply under pressure?
- Compound pressure: time + sunk cost + fatigue.

Technique skills (condition-based waiting, root-cause tracing):
- Application scenario: does it apply the technique correctly?
- Variation scenario: does it handle edge cases?

Pattern skills (mental models):
- Recognition scenario: does it recognize the pattern?
- Application scenario: does it use the mental model?
- Counter case: does it know when not to use it?

Reference skills (documentation, API):
- Search scenario: does it find the right information?
- Application scenario: does it use the found information correctly?

For detailed test methods, see references/skill-testing-guide.md.

For reinforcement patterns that block rationalization escape routes (bulletproofing). Pressure scenarios, counter-example collections, blocking the "this is different" excuse, STOP gates, repeated injection, and so on. See references/bulletproofing-skills.md.

For exact examples, counter-examples, and the authoring procedure in
Condition-Skill-Output form, see references/condition-skill-output-detailed.md.

## Red Flags. Signs to Stop and Start Over.

```markdown
- Writing code without writing the test first
- "I already tested it manually"
- "Writing after the test achieves the same goal"
- "I kept the spirit of it"
- "This is different, because..."

All of these mean: delete the code. Start over with TDD.
```

## The RED-GREEN-REFACTOR Cycle

RED: run the pressure scenario without the skill. What exactly does it do? What excuses does it make? Which pressure triggers the violation?

GREEN: write the minimal skill that addresses those excuses. Run the same scenario with the skill. The agent must now comply.

REFACTOR: when you find a new excuse, add an explicit response. Retest until there are no loopholes.

## Skill-Writing Checklist (TDD Adapted)

RED Phase. Write the failing test:
- Write the pressure scenario (a rule skill needs 3+ compound pressures).
- Run the scenario without the skill. Record the baseline behavior.
- Identify the excuse/failure patterns.

GREEN Phase. Write the minimal skill:
- Name: lowercase, digits, and hyphens only.
- Frontmatter: `name`, `description` (max 1024 chars).
- Description: start with "Use when...", third person, triggers only.
- Include search keywords (errors, symptoms, tools).
- A clear overview and core principle.
- Address the specific failure identified in RED.
- Code inline or a file link.
- One outstanding example (not multi-language).
- Run the scenario with the skill. Confirm compliance.

REFACTOR Phase. Close the loopholes:
- Identify new excuses in testing.
- Add explicit responses (rule skills).
- Build an excuse table from every test iteration.
- Generate a Red Flags list.
- Retest until there are no loopholes.

Quality checks:
- A flowchart only when the decision is not self-evident.
- A Quick Reference table.
- A Common Mistakes section.
- No narrative storytelling.
- Supporting files only for tools or large references.

Distribution:
- Commit to git.
- If broadly useful, consider contributing a PR.

## Using Flowcharts

When to use:
- A non-obvious decision point.
- A process loop that can stop early.
- An "A vs B" decision.

When not to use:
- Reference material -> tables, lists.
- Code examples -> Markdown blocks.
- Linear instructions -> numbered lists.
- Meaningless labels (step1, helper2).

For graphviz style rules, see references/graphviz-conventions.dot.

## Code Examples

One outstanding example beats several mediocre ones.

Choose the most relevant language:
- Testing technique -> TypeScript/JavaScript.
- System debugging -> Shell/Python.
- Data processing -> Python.

A good example:
- Is complete and runnable.
- Is well commented and explains the WHY.
- Comes from a real scenario.
- Shows the pattern clearly.
- Is ready to adapt (not a generic template).

Do not:
- Implement it in 5+ languages.
- Provide a fill-in template.
- Force an example.

## STOP: Before Moving to the Next Skill

After writing any skill, you must stop and complete the distribution process.

Do not:
- Batch-write several skills without testing each one.
- Move to the next one before verifying the current skill.
- Skip testing because batching is efficient.

Distributing an untested skill = distributing untested code. A violation of the quality standard.

## Discovery Workflow

How future Claude finds a skill:

1. A problem occurs ("the test is flaky").
2. Find the SKILL (the description matches).
3. Skim the Overview (is it relevant?).
4. Read the pattern (the Quick Reference table).
5. Load the example (only when implementing).

Optimize for this flow. Place searchable items toward the front, frequently.

## Summary

Writing a skill applies TDD to process documentation. The same Iron Law, the same cycle, the same benefits. If you follow TDD for code, you follow it for skills. It is the same principle applied to documentation.

## Reference Documents

All of this skill's supporting material lives under `references/`. Do not load it when the SKILL.md body is sufficient. Read the relevant file only in the situations below.

- `references/anthropic-best-practices.md`: a summary of Anthropic's official skill-writing guide. Reference it when writing a skill for external release or when you need spec grounding.
- `references/bulletproofing-skills.md`: rationalization-avoidance reinforcement patterns such as pressure scenarios, counter-example collections, blocking the "this is different" excuse, STOP gates, and repeated injection. Reference it when designing tests for rule-enforcement skills.
- `references/condition-skill-output-detailed.md`: exact examples, counter-examples, and the authoring procedure in Condition-Skill-Output form. Reference it when refining the description wording.
- `references/graphviz-conventions.dot`: Graphviz flowchart style rules. Reference it when writing a flowchart.
- `references/persuasion-principles.md`: principles for designing pressure and persuasion scenarios. Reference it when designing RED Phase pressure scenarios.
- `references/review-loop.md`: details of the review loop that iteratively hunts for loopholes after GREEN. Reference it when the REFACTOR step gets stuck.
- `references/skill-testing-guide.md`: detailed test methods by skill type. Reference it when the body "Test Strategy" section is too blunt.
- `references/testing-skills-with-subagents.md`: the procedure for verifying a skill using subagents. Reference it when running a pressure scenario that cannot be tested by hand.
- `references/trigger-eval.md`: a method for evaluating the description trigger accuracy (recall and precision). Reference it when confirming for regressions before and after a Condition-Skill-Output wording change.
- `references/examples/CLAUDE_MD_TESTING.md`: a real case where test rules were injected into CLAUDE.md to verify skill compliance. Reference it when you need a test-setup example.
