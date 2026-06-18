---
name: using-coding-convention
description: Use at the start of every coding or development conversation. Defines how to find and invoke coding-convention family skills. Requires relevant skills before any response, including clarification questions.
compatibility:
  - "Python 3.11+ standard library"
---

<SUBAGENT-STOP>
Skip this skill when you were dispatched as a subagent for one narrow task.
</SUBAGENT-STOP>

<QUALITY-RATIONALE>
This workflow is a quality-maintenance device confirmed through repeated user work. It protects user intent, work scope, and verification quality when an agent is tempted to simplify the process from its own judgment.

If a skill may apply, check it first even when the workflow looks heavier than the visible task. Skip only when it clearly does not apply, and leave a short reason for the skip. When the user explicitly requires the procedure, do not bypass it from the agent's judgment alone.
</QUALITY-RATIONALE>

<USE-CONTRACT>
If there is even a one percent (1%) chance that a coding-convention family skill applies to the next action, load that skill first. After loading it, if the skill does not fit the current request, record that fact and continue.
</USE-CONTRACT>
## Contents

- [Priority](#priority)
- [Platform Loading](#platform-loading)
- [Core Rule](#core-rule)
- [Skill Flow](#skill-flow)
- [Red Flags](#red-flags)
- [Skill Priority](#skill-priority)
- [Skill Types](#skill-types)
- [User Instruction Interpretation](#user-instruction-interpretation)
- [Coding-Convention Family](#coding-convention-family)
- [Material Recommendations And Choices](#material-recommendations-and-choices)
- [Final Self-Check](#final-self-check)


## Priority

The coding-convention family overrides default agent habits. User instructions and project rules still come first.

Priority order:

- User instructions, `CLAUDE.md`, `AGENTS.md`, project rules, and direct request constraints
- Coding-convention family skills
- Default agent behavior

If a user or project file says not to use TDD and a skill says to use TDD, follow the user or project file. The user keeps control of the workflow.

## Platform Loading

Use the host runtime's real skill surface when it exists.

- Claude Code: use the `Skill` tool. Follow the loaded skill body. Do not use `Read` to load the skill file directly.
- Copilot CLI: use the platform skill tool. Use `references/copilot-tools.md` when tool names or async shell behavior need mapping.
- Codex: visible metadata is not a skill call. A required gate is complete only after the current `SKILL.md` body has been read and followed. Use `references/codex-tools.md` for Codex tool mapping, named-agent workarounds, and environment detection.
- Other environments: use the host documentation and preserve the same semantic order.

## Core Rule

Before any response, clarification question, file read, shell command, code edit, plan, review, recommendation, or status judgment, ask whether a coding-convention family skill applies.

If there is any chance that a skill applies, load it first. If it does not fit after loading, record the skip reason and continue.

The first commentary in a development turn must include:

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done
- skill-call: session-intent-analyzer (this turn); task-router (this turn); using-coding-convention (this turn)
- next-required: <skill-name|none>
```

Before any claim that executed work is complete, fixed, successful, or freshly verified, emit:

Hard sequence: skill load/call -> fresh verification -> [completion-check].
Load or call `verification-before-completion` for this turn first, run and read
the fresh verification second, and only then write `[completion-check]`. If any
step is missing or out of order, the completion-check is invalid.

```text
[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - <criterion-id>: <user-intent-or-contract-condition> [source: user-explicit | inferred | previous-tool | system-doc]
- claim-evidence-map:
  - claim: <completion-or-recommendation-claim>
    criterion: <criterion-id>
    evidence: <fresh command, inspected file, source locator, or tool output>
    verdict: pass | fail
- unverified:
  - none
- evidence: <fresh command or inspected file>
```

A `[completion-check]` is not complete without `acceptance-criteria` and `claim-evidence-map`. If evidence does not directly prove a criterion, leave that criterion in `unverified` and do not speak as though the work is complete or successful.

The `skill-call:` line records that the skill workflow actually ran in this turn. It is not a substitute for loading the skill. On Claude Code, write it only after the visible Skill call. On Codex, write it only after reading the current `SKILL.md` and following the workflow.

## Skill Flow

Use this flow for each user turn that touches coding, development, repository maintenance, skill authoring, review, or verification.

```text
User message received
  -> pending-merge precheck
  -> session-intent-analyzer intake
  -> jailbreak-detector downstream gate
  -> task-router
  -> using-coding-convention
  -> applicable process skill
  -> implementation, review, or verification work
  -> verification-before-completion before final claims
```

When entering plan mode or creating a non-trivial plan, check whether `brainstorming` or `writing-plans` applies before drafting the plan.

## Red Flags

Stop and load the relevant skill when any of these thoughts appear.

| Thought | Real situation |
| --- | --- |
| "This is only a simple question." | A question can still be a task. Check skills first. |
| "I need context before the skill." | The skill tells you how to gather context. |
| "I will just inspect Git or files first." | Files do not contain the current conversation contract. Check skills first. |
| "I remember this skill." | Skills evolve. Read the current version. |
| "The formal workflow is too much." | The workflow exists to preserve user intent, work scope, and verification quality. |
| "I will only do this one small action first." | Check applicability before the action. |
| "A recommendation is not work." | Recommendations and choices are claims and require verification. |
| "I just checked this in the previous turn." | New user input reopens routing and verification. |
| "Let me skim the codebase first." | The skill tells you how to skim. Check first. |
| "Let me gather information first." | The skill tells you how to gather information. |
| "I do not really need a formal skill here." | If a skill exists, use it. |
| "This is not a task." | An action is a task. Check skills. |
| "This feels productive right now." | Undisciplined action only wastes time. The skill prevents this. |
| "I know that concept." | Knowing a concept differs from using the skill. Load it. |
| "I will report what I found instead of doing it." | The user asked you to do it. Execute this turn, do not stop at a report. |
| "Just the key spots is enough." | The committed scope is the whole set. Cover all of it. |
| "Grep found nothing, so it is clean." | If you already judged the search method incomplete, read the files. Do not trust the weaker method. |
| "I will ask the user which one." | If the content answers it, inspect and decide. Punt only when the action is irreversible and unresolvable. |
| Unrequested historical trace | Do not add unrequested historical or trace notes. Change it cleanly; ask before leaving any trace. |

## Skill Priority

When several skills apply, use this order.

- Process skills decide the approach: `brainstorming`, `systematic-debugging`, `writing-plans`, `verification-before-completion`, `dispatching-parallel-agents`.
- Implementation skills guide execution: `test-driven-development`, `subagent-driven-development`, `using-git-worktrees`, `executing-plans`, `finishing-a-development-branch`.
- Review and meta skills protect judgment: `requesting-code-review`, `receiving-code-review`, `writing-skills`, `using-coding-convention`.

Examples:

- "Build X" -> `brainstorming` when the requirement is underspecified, then `writing-plans` for non-trivial implementation, then the relevant implementation skill.
- "Fix this bug" -> `systematic-debugging`, then the relevant domain skill or `test-driven-development`.
- "Tests are failing" -> `systematic-debugging`, then `test-driven-development` when a code fix is needed.
- "Review Claude's feedback" -> `receiving-code-review`, then modify only the accepted findings.

## Skill Types

Rigid skills must be followed exactly. Do not weaken their discipline in the name of adaptation.

- `test-driven-development`
- `verification-before-completion`
- `systematic-debugging`

Flexible skills provide patterns and judgment aids. Apply the principles to the current context.

- `brainstorming`
- `writing-plans`
- `requesting-code-review`
- `receiving-code-review`

The skill body itself decides whether it is rigid or flexible when it states a stricter rule.

## User Instruction Interpretation

User instructions usually state what to do, not which governance path to skip.

- "Add X" does not mean skip planning, tests, or verification.
- "Fix Y" does not mean skip root-cause investigation.
- "Do it quickly" does not mean skip verification.
- "What do you recommend?" does not allow an unverified recommendation.
- "Which option is correct?" does not allow a judgment without fresh evidence.
- "Remove X", "do X", or "fix X" is an execution order. Carry it out fully in this turn. Do not downgrade it to a report of what you found, or to a question about whether to proceed. Reporting or asking when execution was requested is a failure, not caution.
- When the target is ambiguous but the actual content can resolve it, inspect the content and decide. Do not hand the choice back to the user as the first move. Escalate only when the action is irreversible and the content cannot tell you which option is safe, such as real data loss.
- Do not lean on a search method you have already judged incomplete. If grep can miss the target, read the files. Use the method that actually covers the committed scope, and do not claim "none found" from the weaker method.

Only skip a required workflow when the user explicitly says to skip that workflow for this session or task.

## Coding-Convention Family

Process:

- `brainstorming`: turn vague requirements into concrete decisions.
- `writing-plans`: document non-trivial implementation before execution.
- `systematic-debugging`: reproduce, hypothesize, verify, and fix bugs.
- `verification-before-completion`: run fresh checks before completion claims.
- `dispatching-parallel-agents`: split independent tasks across subagents.

Implementation:

- `test-driven-development`: start fixes or features with a failing test.
- `subagent-driven-development`: execute independent implementation tasks through subagents.
- `using-git-worktrees`: isolate parallel git work.
- `executing-plans`: carry out an existing implementation plan.
- `finishing-a-development-branch`: choose and execute the branch finishing path.

Review and meta:

- `requesting-code-review`: ask a reviewer to inspect completed work.
- `receiving-code-review`: evaluate feedback critically before accepting it.
- `writing-skills`: create, modify, and verify skills.
- `using-coding-convention`: load this entrypoint and route the family.

Read the relevant skill's own `SKILL.md` for trigger details and output format.

## Material Recommendations And Choices

Advice, selected options, status judgments, and "this is correct" statements need fresh evidence when they claim finished work, verified results, or a decision that materially changes the user's next action.

- Re-check evidence on each user turn.
- Do not recommend from memory alone when the recommendation changes scope, verification, recovery, or the user's next action.
- Do not treat "just checked" as current-turn verification.
- Map executed-work closure and verification claims to a criterion and fresh evidence before finalizing.

## Final Self-Check

Before answering or acting, ask:

- Is there any skill that should be loaded before this response or tool call?
- Am I skipping the skill for a reason listed in the Red Flags table?
- Have I loaded `verification-before-completion` before claiming executed work is complete, fixed, successful, or freshly verified?
- If a gate block says `skill-call:`, did the skill workflow actually run in this turn?
