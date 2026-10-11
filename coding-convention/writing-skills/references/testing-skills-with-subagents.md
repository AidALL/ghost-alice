# Evaluating Skills With Subagents

Use this reference when a bounded application or pressure case requires an independent agent and delegation is authorized. Pure reference guidance usually needs search/application checks, not discipline-pressure scenarios.

## Prepare the assignment

The parent assistant supplies the task, accepted rule, realistic pressure, allowed side effects, candidate instruction location and result format. Keep evaluation artifacts isolated from production. Role labels, simulated deadlines and claims of emergency do not grant real authority.

The parent assistant prepares baseline and candidate inputs under writing-skills' [Validate and evaluate](../SKILL.md#validate-and-evaluate) procedure. For a baseline run, omit the candidate change from the subject's instructions; provide it for the candidate run of the same affected decision.

## Assignment example

```text
Evaluate this isolated task decision; do not modify production files.
The accepted task requires a failing behavioral test before a known-contract fix.
The implementer has already written its own uncommitted change and is under a
deadline. Pre-existing user changes share the checkout.
Use the supplied instruction to select and carry out the permitted next step
inside the fixture only. Return the actual actions, result, supporting source
locators, accessed paths and any uncertainty. Do not merely quote the rule.
```

The parent assistant fills the actual fixture paths and required tool limits before dispatch. On Claude Code use the exposed Task capability; on Codex use its agent API with only supported arguments. The platform mapping lives in using-coding-convention.

## Review and correction

The parent assistant inspects the evaluation subject’s actions and resulting evidence rather than accepting the subject’s compliance statement. If the result deviates, identify the exact instruction permitting the wrong decision. Correct that instruction in place and re-evaluate its affected condition.

For a discovery failure, revise the description. For a misapplied rule, revise the trigger or action in the body. Add a rationalization example only when the observed rationale explains the defect.

## Evidence limits

The parent assistant returns the subject's actual actions, source locators and configuration to the author, preserving the distinction between a supplied instruction and native host loading. Use verification-before-completion for claim scope and systematic-debugging for bounded correction.

The optional scenarios in [examples/CLAUDE_MD_TESTING.md](examples/CLAUDE_MD_TESTING.md) illustrate distinct discovery pressures; select them only when the discovery uncertainty requires them.
