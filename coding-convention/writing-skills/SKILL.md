---
name: writing-skills
description: "Writes a new skill or changes an existing one in its folder (SKILL.md, references and scripts) and tests it."
compatibility:
  - "Python 3.11+ standard library"
---

# Writing Skills

The author is the assistant editing these reusable instructions; an evaluation subject is the model assigned a bounded test prompt.

The author changes reusable skill guidance from observed behavior or a direct source-contract contradiction, then validates the package and the affected decision. Read test-driven-development for executable changes; an instruction-only contradiction can use preserved source evidence instead of an invented failed agent run.

## Baseline and scope

Before editing, identify the exact skill decision that is wrong or ambiguous. Retain the actual baseline outcome or source quotation and locator. A passing baseline stays a pass; a predicted failure is not an observed failure.

Write a reusable technique, pattern or reference when repeated work benefits from it. Keep one-off project rules in project instructions. Do not create a skill merely because a regular expression or existing automation can enforce the constraint.

Define the affected evaluation before editing. Include only cases whose outcomes can change acceptance or the next correction. For executable behavior, the author follows test-driven-development for the code and test being changed.

## Author the package

Use `SKILL.md` for core guidance, conditionally loaded `references/` for detail, and `scripts/` for executable support. At an operational handoff, write enough for a reader without prior conversation to act: identify the caller and trigger, the authoritative origin and required shape of transferred values, the exposed tool or actual API/CLI invocation, the result and its storage owner, and the conditional next action. Distinguish the assistant's meaning judgment from code that authenticates its recorded judgment. Put a shared procedure in one owning reference and link callers to its section; do not repeat this field list in every sentence or replace an executable transfer with an actor label. Mark illustrative prompts, schemas and data as examples rather than ambient task authority.

The frontmatter must contain a directory-matching lowercase-hyphen name and a description of trigger conditions. Keep descriptions within 1024 characters; state compatibility when the workflow depends on an environment.

In the body, state the workflow's purpose, applicability, procedure, output and failure modes. Use a quick-reference table when it improves selection. Prefer one complete relevant example over multiple shallow language variants. Long code and reference tables belong in supporting files; every reference in the package needs a clear loading condition.

Do not add hypothetical defenses or repeat a prohibition merely to increase emphasis. If a real evaluation shows rationalization, revise the instruction that allowed the wrong decision.

## Validate and evaluate

1. Preserve baseline evidence before changing the candidate.
2. Edit the affected body, callers and references to remove the demonstrated contradiction.
3. Read `official-docs/derived/skill-compliance-checklist.md` and complete Phases 1 through 5 for every changed package before evaluation.
4. Run the affected pressure, application or source-contract check required by the task and record the actual outcome and limits.
5. If an observation contradicts a requirement, correct the implicated instruction and repeat only the affected checks.
6. Distribute or commit only under the user's existing authorization and after required validation succeeds.

Coordinated changes may span packages; each changed package still needs its own disposition.

Verification-before-completion owns useful-check selection, evidence reuse, original source/time and completion claims. Systematic-debugging owns failed-round accounting and bounded continuation. Do not duplicate either policy in a skill under repair.

## Failure modes

- Do not relabel a passing baseline as a failure.
- Do not backdate missing baseline evidence or delete inconvenient results.
- Do not add a new report, hook or mandatory record when existing task context already preserves the evidence.

## Conditional references

| Authoring uncertainty | Reference |
| --- | --- |
| Official skill design principles | [anthropic-best-practices.md](references/anthropic-best-practices.md) |
| Rule rationalization exposed by a case | [bulletproofing-skills.md](references/bulletproofing-skills.md) |
| Trigger, workflow and output separation | [condition-skill-output-detailed.md](references/condition-skill-output-detailed.md) |
| Flowchart notation | [mermaid-conventions.md](references/mermaid-conventions.md) |
| Pressure dimensions for a case | [persuasion-principles.md](references/persuasion-principles.md) |
| A post-evaluation contradiction | [review-loop.md](references/review-loop.md) |
| Test method by skill type | [skill-testing-guide.md](references/skill-testing-guide.md) |
| Authorized subagent evaluation | [testing-skills-with-subagents.md](references/testing-skills-with-subagents.md) |
| Trigger precision and recall | [trigger-eval.md](references/trigger-eval.md) |
| Discovery scenario example | [CLAUDE_MD_TESTING.md](references/examples/CLAUDE_MD_TESTING.md) |
