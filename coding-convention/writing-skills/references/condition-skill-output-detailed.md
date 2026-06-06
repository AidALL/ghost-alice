# Claude Search Optimization. Detailed Guide.

## Contents

- [1. Rich Description Field](#1-rich-description-field)
  - [Why does this matter?](#why-does-this-matter)
  - [Bad examples](#bad-examples)
  - [Good examples](#good-examples)
  - [Writing guidelines](#writing-guidelines)
- [2. Keyword Coverage](#2-keyword-coverage)
- [3. Descriptive Naming](#3-descriptive-naming)
- [4. Token Efficiency (Critical)](#4-token-efficiency-critical)
  - [Techniques](#techniques)
- [5. Cross-Referencing Other Skills](#5-cross-referencing-other-skills)

## 1. Rich Description Field

Purpose: Claude reads the description and decides whether it should read this skill right now. It must answer "Should I load this skill now?".

Format: start with "Use when..." and focus on the trigger conditions.

Critical: Description = When to Use, NOT What the Skill Does

Do not summarize the skill's process or workflow in the description.

### Why does this matter?

Test result: when the description summarizes the skill's workflow, Claude is likely to follow the description instead of the body. A description such as "execute the implementation plan while doing code review between tasks" makes Claude run the review only once, even though the flowchart clearly shows two passes (spec compliance, code quality).

What happens when the description simply changes to "use when executing an implementation plan as independent work in the current session"? Claude reads the body and correctly follows the two-stage review.

Trap: a description that summarizes the workflow becomes a shortcut Claude takes. The skill body becomes a document it ignores.

### Bad examples

```yaml
# ❌ Workflow summary. Claude may follow the description and ignore the body.
description: Execute the implementation plan. Dispatch a subagent per task, code review between tasks

# ❌ Too much process detail
description: Use TDD. Test first, watch it fail, minimal code, refactor

# ❌ Too abstract, no trigger
description: For asynchronous testing

# ❌ First person
description: I can help when your async tests are flaky
```

### Good examples

```yaml
# ✅ Trigger only, no workflow summary
description: Use when executing an implementation plan as independent work in the current session

# ✅ Trigger only
description: Use before implementing a feature or a bug fix

# ✅ Technology-specific, clear trigger
description: Use when handling auth redirects with React Router

# ✅ Concrete symptom, technology-agnostic
description: Use when a test has a race condition, timing dependency, or inconsistent pass/fail
```

### Writing guidelines

- Use concrete triggers, symptoms, and situations (the things that signal the problem)
- Describe the problem (race condition, inconsistency) rather than technology-specific symptoms (setTimeout, sleep)
- Be technology-agnostic unless the skill itself is technology-specific
- If the skill is technology-specific, state it in the trigger
- Third person (it is injected into the system prompt)
- Never summarize the skill's process or workflow

## 2. Keyword Coverage

Use the words Claude will search for:

- Error messages: "Hook timed out", "ENOTEMPTY", "race condition"
- Symptoms: "flaky", "hanging", "zombie", "pollution"
- Synonyms: "timeout/hang/freeze", "cleanup/teardown/afterEach"
- Tools: actual commands, library names, file types

## 3. Descriptive Naming

The skill name is also search optimization.

Active voice, verb first:
- ✅ `writing-skills` not `skill-writing`
- ✅ `condition-based-waiting` not `async-test-helpers`

The gerund (-ing) pattern fits process names well:
- `writing-skills`, `testing-skills`, `debugging-with-logs`
- Describes an active, ongoing action

## 4. Token Efficiency (Critical)

Problem: frequently loaded skills and getting-started are included in every conversation. Tokens are precious.

Target word counts:
- getting-started workflows: < 150 words each
- frequently loaded skills: < 200 words total
- other skills: < 500 words (still concise)

### Techniques

Move detail into tool help:

```markdown
# ❌ Documenting every flag in SKILL.md
search-conversations supports --text, --both, --after DATE, --before DATE, --limit N

# ✅ Reference --help
search-conversations supports several modes and filters. See --help for details.
```

Use cross-references:

```markdown
# ❌ Repeating workflow detail
When searching, dispatch a subagent with the template...
[20 lines of repeated instructions]

# ✅ Reference another skill
Always use a subagent (50-100x context savings). Required: see coding-convention:other-skill-name.
```

Compress examples:

```markdown
# ❌ Detailed example (42 words)
Human partner: "How did we handle auth errors in React Router before?"
You: I will search past conversations for the React Router auth pattern.
[Dispatch subagent: search query]

# ✅ Minimal example (20 words)
Partner: "How did we handle React Router auth errors?"
You: Searching...
[Subagent → synthesis]
```

Remove duplication:
- Do not repeat the content of a cross-referenced skill
- Do not explain what is self-evident from the command
- Do not include multiple examples of the same pattern

Verification:

```bash
wc -w skills/path/SKILL.md
# getting-started: < 150 target
# frequently loaded: < 200 total
```

## 5. Cross-Referencing Other Skills

When referencing another skill:

Use the skill name only, with an explicit required marker:
- ✅ Good: `Required prerequisite: you must understand coding-convention:test-driven-development`
- ✅ Good: `Required background: you must understand coding-convention:systematic-debugging`
- ❌ Bad: `See skills/testing/test-driven-development` (unclear whether it is required)
- ❌ Bad: `@skills/testing/test-driven-development/SKILL.md` (forced load, wasted context)

Why you must not use @ links: the `@` syntax loads the file immediately, consuming 200k+ context before it is needed.
