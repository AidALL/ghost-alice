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
- [Evidence Before Instruction Changes](#evidence-before-instruction-changes)
- [Test Strategy](#test-strategy)
- [Critical Rules: Evaluation Evidence](#critical-rules-evaluation-evidence)
- [The RED-GREEN-REFACTOR Cycle](#the-red-green-refactor-cycle)
- [Skill-Writing Checklist (TDD Adapted)](#skill-writing-checklist-tdd-adapted)
- [Using Flowcharts](#using-flowcharts)
- [Code Examples](#code-examples)
- [STOP: Before Moving to the Next Skill](#stop-before-moving-to-the-next-skill)
- [Discovery Workflow](#discovery-workflow)
- [Summary](#summary)
- [Reference Documents](#reference-documents)


## Overview

Define cases before changing a skill and record the actual baseline behavior. An observed failure or direct source-contract mismatch identifies the affected repair; a baseline pass remains a pass. Apply the candidate, verify the affected conditions with appropriate pressure or application cases, and repair newly evidenced mismatches. Do not infer behavioral improvement from a source edit or two passing variants.

Core principle: distinguish an observed behavior failure from a source-contract contradiction. Preserve both the original evidence and its limits. A passing baseline is useful evidence; it cannot be relabeled as failure.

Required background: you must first understand coding-convention:test-driven-development. That skill defines the foundation of the RED-GREEN-REFACTOR cycle.

## What Is a Skill?

A skill is a reference guide to a proven technique, pattern, or tool. It helps future Claude instances find and apply an effective approach.

A skill is a reusable technique, pattern, and tool. It is not a narrative of one time you solved a problem.

## Applying TDD to Writing Skills

| TDD concept | Writing skills |
|---------|--------|
| Test case | A pressure scenario including subagents |
| Production code | The skill document (SKILL.md) |
| Baseline | Actual pass/fail behavior, or an exact source-contract mismatch |
| GREEN success | The agent complies when the skill is present |
| Repair | Resolve observed mismatches and preserve unaffected evidence |

Use baseline evidence, a sufficiently scoped repair and affected verification. For executable behavior changes, follow the owning test-driven-development workflow. For instruction-only contradictions, preserve the exact source mismatch before editing and evaluate the corrected condition.

## Closed-Loop Checkpoints

- At baseline: bind the actual result or source mismatch to the named uncertainty.
- After editing: review the package body, examples, references and calls together, then complete the official Phase 1 through Phase 5 checklist before evaluation.
- At evaluation: check affected conditions and relevant negative controls. Report the checked limits; repeat only when a real change or unresolved variance can change the decision.
- When a mismatch appears, reopen the affected artifact, mapping, composition or inferred premise. Move directly to the useful focus level and return to the accepted work after repair; the previous step is not always the cause.

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
- Core Pattern (for technique/pattern skills): a current working pattern and a clearly labeled failure counterexample.
- Quick Reference: a table or bullet list for skimming.
- Implementation: inline when simple, a file link when long.
- Common Mistakes: failures and solutions.

## Claude Search Optimization

Making sure future Claude can find the skill is important.

### The Role of the Description Field

The description must do only two things:
1. State the trigger conditions specifically.
2. No process or workflow description.

A workflow summary in a description can invite metadata-only execution. Put the trigger in the description and the complete procedure in the body. For a two-stage review, require the body to define both spec compliance and code quality; a short phrase such as "code reviews" does not specify their order or acceptance conditions.

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

## Evidence Before Instruction Changes

```
Do not change a skill without decision-relevant baseline evidence and an affected verification plan
```

This applies to new skills and package changes, including references. Preserve the baseline before editing. If baseline capture was missed, record the gap honestly and recover available source evidence; do not backdate an evaluation or delete evidence to create a cleaner story. Do not distribute a candidate before its required package validation and affected checks pass.

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

For exact examples, counter-examples, and the authoring procedure in Condition-Skill-Output form, see references/condition-skill-output-detailed.md.

## Critical Rules: Evaluation Evidence

- A predicted failure is not an observed failure.
- Two passes do not prove the candidate improved behavior.
- A source check does not prove native skill loading or host adoption.
- A format-only check does not establish semantic compliance.
- An unchanged re-run needs a named uncertainty that can change the decision.

Repair the unsupported claim or affected instruction; preserve the original evidence.

## The RED-GREEN-REFACTOR Cycle

BASELINE: record what the existing instruction causes in a relevant case, including compliance. For a direct source contradiction, preserve its exact locator and affected contract. Do not invent a failed agent run.

CANDIDATE: repair the cause to sufficient depth, including affected references and callers. Complete official Phase 1 through Phase 5 before running the affected scenario with the candidate. The observed decision must satisfy the current contract.

REFACTOR: address an observed rationalization or contradiction and re-test its affected condition. Stop when no live uncertainty can change the next decision; retain supported passes and record the checked limits.

## Skill-Writing Checklist (TDD Adapted)

Baseline:
- Choose pressure or application cases that cover the actual uncertainty. Combine pressures when their interaction matters.
- Preserve actual baseline behavior and exact source contradictions before editing.
- Identify observed failures and retain passes without claiming causal improvement.

Candidate and evaluation:
- Name: lowercase, digits, and hyphens only.
- Frontmatter: `name`, `description` (max 1024 chars).
- Description: start with "Use when...", third person, triggers only.
- Include search keywords (errors, symptoms, tools).
- A clear overview and core principle.
- Address the observed failure or source-contract mismatch to sufficient depth.
- Code inline or a file link.
- One outstanding example (not multi-language).
- Complete the official package checklist, Phase 1 through Phase 5, before evaluation.
- Run the affected scenario with the skill and record the actual decision.

Conditional repair:
- If testing exposes an actual contract gap, inspect its cause and repair the affected instruction or reference.
- Add an explicit counter, rationalization table or red flag only when it helps resolve that observed gap.
- Change trigger wording only when discovery is implicated.
- Re-test affected conditions after a real change or contradiction; retain passes and stop when another unchanged run cannot change the decision.

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

Validate each changed package before its evaluation or distribution. Coordinated edits may span related packages when the authorized contract is systemic; retain separate package dispositions and affected evidence. Do not use batching to skip a package's required phases or to imply that one package's pass verifies another.

## Discovery Workflow

How future Claude finds a skill:

1. A problem occurs ("the test is flaky").
2. Find the SKILL (the description matches).
3. Skim the Overview (is it relevant?).
4. Read the pattern (the Quick Reference table).
5. Load the example (only when implementing).

Optimize for this flow. Place searchable items toward the front, frequently.

## Summary

Bind a skill change to actual evidence, validate the whole affected package, and evaluate the decision it must support. Retain unchanged valid evidence and stop when further checking cannot change the next decision.

## Check sufficiency and repair limits

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.

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
- `references/examples/CLAUDE_MD_TESTING.md`: a scenario set and documentation variants for verifying skill discovery and compliance. Reference it when you need a test-setup example.
