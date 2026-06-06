# Code Quality Reviewer Prompt Template

Use this template when dispatching the code quality reviewer subagent.

Purpose: verify that the implementation is well built (clean, tested, maintainable).

Precondition: dispatch only after the spec compliance review has passed.

```
Task tool (coding-convention:code-reviewer):
  Use template at requesting-code-review/references/code-reviewer.md

  WHAT_WAS_IMPLEMENTED: [from implementer's report]
  PLAN_OR_REQUIREMENTS: Task N from [plan-file]
  BASE_SHA: [commit before task]
  HEAD_SHA: [current commit]
  DESCRIPTION: [task summary]
```

## In addition to standard code quality concerns, the reviewer must check the following

- Does each file have one clear responsibility with a well-defined interface?
- Are the units decomposed so they can be understood and tested independently?
- Does the implementation follow the plan's file structure?
- Did this implementation already create a large new file or grow an existing file substantially? (Do not flag the pre-existing file size. Focus on the part this change contributed.)

Reviewer returns: Strengths, Issues (Critical/Important/Minor), Assessment

## Field guide

□ Slots to fill

- `WHAT_WAS_IMPLEMENTED`: taken from the implementer's report.
- `PLAN_OR_REQUIREMENTS`: `Task N from <plan-file>` format.
- `BASE_SHA`: the commit before the task started.
- `HEAD_SHA`: the current commit.
- `DESCRIPTION`: a one-line summary of the task.

□ Namespace and path

- When calling the Task tool, standardize the agent namespace to `coding-convention:code-reviewer`
- The template path is `requesting-code-review/references/code-reviewer.md` (after porting, it moved under references/)
