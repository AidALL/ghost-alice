---
name: writing-plans
description: Use when requirements for a multi-step task are known and before touching code. Documents everything as bite-sized tasks for an engineer with zero codebase context. Use for implementation plans, task decomposition, and planning before TDD.
calls:
  - "union:subagent-driven-development:plan-execution"
  - "union:executing-plans:plan-execution"
compatibility:
  - "Python 3.11+ standard library"
---

# Writing Plans
## Contents

- [Overview](#overview)
- [Scope Check](#scope-check)
- [File Structure](#file-structure)
- [Bite-Sized Task Units](#bite-sized-task-units)
- [Plan Document Header](#plan-document-header)
- [Task Structure](#task-structure)
- [No Placeholders](#no-placeholders)
- [Things to Remember](#things-to-remember)
- [Self-Review](#self-review)
- [Execution Handoff](#execution-handoff)


## Overview

Write a comprehensive implementation plan assuming the engineer has no context
on the codebase. Document everything they need to know. For each task: the
files to touch, the code, the tests, the docs worth checking, and how to test.
Give the whole plan as bite-sized tasks. DRY, YAGNI, TDD, and frequent commits.

Assume the engineer is a capable developer but knows almost nothing about our toolset or problem domain. Assume they do not know good test design well.

○ Declare at the start: "I will write the implementation plan with the writing-plans skill."

○ Context: this skill must run in the dedicated worktree created by the brainstorming skill.

○ Plan storage location: `.tmp/implementation-plans/YYYY-MM-DD-<feature-name>.md`

- If the user specifies a different location, that specification overrides the default.
- The plan document is not canonical docs. It is a scratch/handoff artifact produced before execution. Move only the content you intend to promote into policy or contract into a separate `docs/policies/` document.

## Scope Check

If the spec covers several independent subsystems, it should have been split into sub-project specs during the brainstorming stage. If it was not split, propose breaking it into separate plans, one per subsystem. Each plan must produce software that works and can be tested on its own.

## File Structure

Before defining tasks, map which files will be created or modified and write down the responsibility of each. The decomposition decision is locked here.

- Design units with clear boundaries and well-defined interfaces. Each file holds one clear responsibility.
- Code that fits in context at once is reasoned about best. Edits are more stable when files are small and focused. Prefer small, focused files over large files that do too much.
- Keep files that change together in the same place. Separate by responsibility, not by technical layer.
- In an existing codebase, follow the established patterns. If the codebase uses large files, do not restructure unilaterally. That said, if a file you are modifying has grown unwieldy, including a split in the plan is reasonable.

This structure informs the task decomposition. Each task must produce a self-contained change that is meaningful on its own.

## Bite-Sized Task Units

Each step is a single action (2-5 minutes).

- "Write a failing test" is A step.
- "Run to confirm the failure" is A step.
- "Implement the minimal code to make it pass" is A step.
- "Run the test and confirm it passes" is A step.
- "Commit" is A step.

## Plan Document Header

Every plan starts with this header.

```markdown
# [feature name] Implementation Plan

> For the agent worker: required sub skill. Implement task by task with `subagent-driven-development` (recommended) or `executing-plans`. Track steps with checkbox (`- [ ]`) syntax.

Goal: [one sentence on what this builds]

Architecture: [the approach in 2-3 sentences]

Tech stack: [core technologies and libraries]

---
```

## Task Structure

````markdown
### Task N: [component name]

Files

- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

- [ ] Step 1: write a failing test

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

- [ ] Step 2: confirm the test fails

Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL with "function not defined"

- [ ] Step 3: minimal implementation

```python
def function(input):
    return expected
```

- [ ] Step 4: confirm the test passes

Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

- [ ] Step 5: commit

```bash
git add tests/path/test.py src/path/file.py
git commit -m "feat: add specific feature"
```
````

## No Placeholders

Each step must carry the real content the engineer needs. The following are plan failures. Never write them.

- "TBD", "TODO", "implement later", "fill in the details"
- "add appropriate error handling" / "add validation" / "handle edge cases"
- "write tests for the above" (without actual test code)
- "similar to Task N" (repeat the code. The engineer may not read tasks in order.)
- a step that explains only what to do without showing how to do it (a code step requires a code block)
- a reference to a type, function, or method that is not defined in any task

## Things to Remember

- always exact file paths
- complete code in every step. If a step changes code, show the code.
- exact commands and expected output
- DRY, YAGNI, TDD, and frequent commits

## Self-Review

After writing the complete plan, look at the spec with fresh eyes and check it against the plan. This is a checklist you run yourself, not a subagent dispatch.

□ 1. Spec coverage

Scan each section and requirement of the spec. Can you point to the task that implements it? List any omissions.

□ 2. Placeholder scan

Search the plan for the patterns in the "No Placeholders" section above. Red flag. Fix them.

□ 3. Type consistency

Do the types, method signatures, and attribute names used in later tasks match those defined in earlier tasks? If a function called `clearLayers()` in Task 3 shows up as `clearFullLayers()` in Task 7, that is a bug.

When you find an issue, fix it inline. No re-review needed. Just fix it and proceed. If a spec requirement has no task, add a task.

If the self-review alone leaves you uneasy, dispatch a subagent to run one more review of the plan document. The dispatch prompt template is defined in references/plan-document-reviewer-prompt.md.

## Execution Handoff

After saving the plan, present the execution options.

```
Plan complete and saved to .tmp/implementation-plans/<filename>.md.
Two execution options:

1. Subagent-Driven (recommended): dispatch a new subagent per task, review between tasks, fast iteration.

2. Inline execution: run in this session with executing-plans, executing checkpoint batches.

Which one?
```

□ When Subagent-Driven is chosen

- required sub skill: `subagent-driven-development`
- a new subagent per task plus a two-stage review

□ When Inline execution is chosen

- required sub skill: `executing-plans`
- batch execution with review checkpoints
