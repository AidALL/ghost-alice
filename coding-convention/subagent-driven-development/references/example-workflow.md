# Subagent-Driven Development Example Workflow

This file is a reference that splits out the long example from `subagent-driven-development/SKILL.md`.

```text
You: Execute this plan with Subagent-Driven Development.

[Read the plan file once: .tmp/implementation-plans/feature-plan.md]
[Extract the full text and context of all 5 tasks]
[Create a TodoWrite with every task]

Task 1: hook install script

[Bring in the Task 1 text and context (already extracted)]
[Dispatch an implementation subagent with the full task text + context]

Implementer: "Before I start, do I install the hook at the user level or the system level?"

You: "User level (~/.config/coding-convention/hooks/)"

Implementer: "Understood. Implementing..."
[Later] Implementer:
  - Implemented the install-hook command
  - Added tests, 5/5 passing
  - Self-review: found the --force flag was missing, added it
  - Committed

[Dispatch the spec-compliance reviewer]
Spec reviewer: OK: spec compliant. All requirements met, nothing extra.

[Obtain the git SHA, dispatch the code-quality reviewer]
Code reviewer: Strengths: good test coverage, clean. Issues: none. Approved.

[Mark Task 1 complete]

Task 2: recovery mode

[Dispatch the implementer]
Implementer: [proceeds without questions]
Implementer:
  - Added verify and repair modes
  - 8/8 tests passing
  - Self-review: all good
  - Committed

[Dispatch the spec-compliance reviewer]
Spec reviewer: FAIL: issues:
  - Missing: progress reporting (the spec says "report every 100 items")
  - Extra: --json flag (not requested)

[Implementer fixes the issues]
Implementer: removed the --json flag, added progress reporting

[Spec reviewer re-reviews]
Spec reviewer: OK: now spec compliant

[Dispatch the code-quality reviewer]
Code reviewer: Strengths: solid. Issues (Important): magic number (100)

[Implementer fixes]
Implementer: extracted the PROGRESS_INTERVAL constant

[Code reviewer re-reviews]
Code reviewer: OK: approved

[Mark Task 2 complete]

...

[After all tasks]
[Dispatch the final code-reviewer]
Final reviewer: all requirements met, ready to merge

Done!
```
