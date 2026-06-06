---
name: executing-plans
description: Use when an implementation plan already exists. Execute it in a separate session with review checkpoints.
calls:
  - "hard:finishing-a-development-branch"
  - "hard:using-git-worktrees"
compatibility:
  - "Python 3.11+ standard library"
---

# Executing Plans
## Contents

- [Overview](#overview)
- [Process](#process)
  - [Step 1: Load and review the plan](#step-1-load-and-review-the-plan)
  - [Step 2: Execute the tasks](#step-2-execute-the-tasks)
  - [Step 3: Finish development](#step-3-finish-development)
- [Failure modes and cautions](#failure-modes-and-cautions)
- [When to stop and ask for help](#when-to-stop-and-ask-for-help)
- [When revisiting an earlier step](#when-revisiting-an-earlier-step)
- [Things to remember](#things-to-remember)
- [Integration](#integration)


## Overview

Load the plan, review it critically, execute every task, and report on completion.

Announce at the start: "I'm using the executing-plans skill to implement this plan."

Note: when running on a platform that supports subagents (Claude Code, Codex, and so on), use coding-convention:subagent-driven-development instead of this skill. Execution efficiency is much higher in an environment with subagent access.

## Process

### Step 1: Load and review the plan

1. Read the plan file.
2. Review it critically. Identify questions or concerns about the plan.
3. If there are concerns: discuss them with the user partner before starting.
4. If there are no concerns: create the TodoWrite and proceed.

### Step 2: Execute the tasks

For each task:
1. Mark it in_progress.
2. Follow each step exactly (the plan is composed of bite-sized steps).
3. Run verification as specified.
4. Mark it completed.

### Step 3: Finish development

When every task is completed and verified:
- Announce: "I'm using the finishing-a-development-branch skill to complete this work."
- Required sub-skill: use coding-convention:finishing-a-development-branch.
- Follow that skill to verify, present options, and execute the choice.

## Failure modes and cautions

- Do not mistake the plan file for execution commands.
- Do not arbitrarily add files, tests, or refactors that are not in the plan.
- Do not pass over a blocking situation by guessing.
- Do not skip verification before completion or the finishing-a-development-branch call.

## When to stop and ask for help

Stop execution immediately in the following situations:
- A blocking situation occurs (a missing dependency, a test failure, an unclear instruction).
- The plan has a critical gap that prevents starting.
- You do not understand an instruction.
- Verification fails repeatedly.

Asking clearly is better than guessing.

## When revisiting an earlier step

When returning to review (Step 1):
- The partner updates the plan based on feedback.
- The base approach needs to be reconsidered.

Do not force your way through a blocking situation. Stop and ask.

## Things to remember

- Review the plan critically first.
- Follow the plan steps exactly.
- Do not skip verification.
- Use a skill when the plan references it.
- When blocked, stop and do not guess.
- Do not start implementation on the main/master branch without explicit user consent.

## Integration

Required workflow skills:
- coding-convention:using-git-worktrees - required: set up an isolated workspace before starting.
- coding-convention:writing-plans - writes the plan that this skill executes.
- coding-convention:finishing-a-development-branch - finishes development after every task is complete.
