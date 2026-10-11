---
name: requesting-code-review
description: Use after completing major functionality, before merging, or when a pull request has new commits, automated findings, or unresolved review threads.
compatibility:
  - "Python 3.11+ standard library"
---

# Requesting Code Review

Dispatch the `code-reviewer` subagent to catch issues before they accumulate. The reviewer receives only the context prepared for evaluation. It never receives the session history. This keeps the reviewer focused on the work output rather than on your thought process, and preserves your own context for follow-up work.

The parent assistant requests review at required milestones and when a specific unresolved concern warrants independent inspection.
## When to Request a Review

□ Required

- After each task in subagent-driven-development
- After completing major functionality
- Before merging to main

□ Optional but valuable

- When stuck (a fresh perspective)
- Before refactoring (a baseline check)
- After fixing a complex bug

## How to Request

□ 1. Establish the review target

Use the accepted task's starting commit and current head for committed work. Identify any uncommitted diff separately; a commit range cannot include it. Do not guess the base from HEAD~1 or a commit-message match.

□ 2. Dispatch the code-reviewer subagent

Use the host's exposed agent API and the actual `references/code-reviewer.md` template. Supply a role parameter only when the host schema defines one.

Placeholders

- `{WHAT_WAS_IMPLEMENTED}`: what you just built.
- `{PLAN_OR_REQUIREMENTS}`: what you were supposed to do.
- `{BASE_SHA}`: the starting commit.
- `{HEAD_SHA}`: the ending commit.
- `{DESCRIPTION}`: a short summary.

For publication review, the parent supplies the preflight matrix produced by finishing-a-development-branch Step 1. The reviewer reports any missing required result or unaccounted environment difference; that owning procedure determines affected reruns before publication.

□ 3. Respond to the feedback

- Evaluate each finding through receiving-code-review before changing code.
- Fix valid critical and important findings before the dependent integration.
- Retain minor advisory findings without silently adding them to the accepted scope.
- Reject incorrect findings with code or test evidence.

## Remote Pull Request Review Gate

A local reviewer and passing CI do not establish that remote review is complete. Conversely, a clean review does not establish successful CI. When the work has a pull request, perform this gate before integration, including a direct fast-forward push to the base branch. Use the provider API or its authenticated CLI (`gh` for GitHub); a checks summary alone is insufficient.

1. Read the PR's current head SHA and the configured or requested review sources. Fetch every page of submitted reviews, inline comments, PR conversation summaries, and review threads with their resolution state. Include automation results delivered as comments or reactions, not only formal approvals. Read relevant outstanding findings from earlier PRs in this change's lineage, including merged PRs, when their affected code is still present; do not turn this into an unrelated repository-wide audit.
2. Bind the review result to the current head. Record each source's reviewed commit, completion state, and findings. Pending, unavailable, stale, or unidentifiable results are not a clean review. An empty comment list is not proof that a requested reviewer finished. If no remote reviewer is configured or requested, record that fact after inspection rather than inventing a new required service.
3. Reconcile each finding with the current implementation and the user's actual requirements. Reproduce a plausible bug, fix a valid issue, or record evidence for rejecting an incorrect or superseded finding. A bot's priority label is not authority to override user instructions. Unresolved threads require an explicit disposition; a dismissed review, resolved thread, or merged earlier PR does not by itself prove that its underlying issue was fixed.
4. After a fix, amend, rebase, or squash changes the head, reread the remote state and obtain the required review for that head. Preserve earlier findings until current code and evidence resolve them. Request or retrigger a review through the configured workflow when needed and already authorized; this skill does not independently authorize posting comments or messages. Continue useful authorized fixes while review is pending, and report an unavailable review honestly instead of silently substituting CI or local review.
5. Immediately before integration, read the head, review state, and applicable remote CI results again. Integrate only the reconciled head, with no unaddressed valid blocking findings, all required reviews complete, and successful applicable remote CI for that exact head. Local equivalents, an older green SHA, pending jobs, or silently skipped required jobs do not satisfy remote CI. If the head moved, return to step 2 and repeat affected local preflight before another push. Follow any explicit user decision to waive or change review requirements, but report the resulting limitation and never label an unperformed review clean.

Record a compact result with the PR URL, head SHA, review sources and completion evidence, finding dispositions with code/test evidence, unresolved items, and integration decision. A provider's successful merge response is not retrospective evidence that this gate ran.

## Failure modes and result

Do not substitute a reviewer's confidence or CI status for the required review result. receiving-code-review owns each finding's disposition; retain unresolved conditions before dependent integration.

Template: `references/code-reviewer.md`. Verification-before-completion owns final claim scope and evidence; a reviewer verdict alone does not establish integration readiness.
