---
name: verification-before-completion
description: Use before claiming completion, fixes, passes, commits, or PR creation. Requires running verification commands and reading their output before making success claims. Evidence always comes before claims.
compatibility:
  - "Python 3.11+ standard library"
---

# Verification Before Completion
## Contents

- [Overview](#overview)
- [Iron Law](#iron-law)
- [Acceptance Criteria Iron Law](#acceptance-criteria-iron-law)
- [Hard Finalization Order](#hard-finalization-order)
- [Gate Function](#gate-function)
- [Completion-Check Format](#completion-check-format)
- [Common Failures](#common-failures)
- [Red Flags](#red-flags)
- [Rationalization Defense](#rationalization-defense)
- [Verification Patterns](#verification-patterns)
- [Why It Matters](#why-it-matters)
- [External Tool Web-Search-First Gate](#external-tool-web-search-first-gate)
- [Evaluator Artifact Contract](#evaluator-artifact-contract)
- [When To Apply](#when-to-apply)
- [Final Self-Check](#final-self-check)


## Overview

Completion without fresh evidence is not efficiency. It is a lie.

Core principles:

- Evidence always comes before the claim.
- The letter of the rule and the spirit of the rule are the same rule.
- A surrounding signal is not direct proof unless it satisfies the relevant criterion.

## Iron Law

```text
Do not claim completion without fresh verification evidence from this turn.
```

If the verification command or inspection did not run in this message, do not claim that it passed.

## Acceptance Criteria Iron Law

```text
No acceptance-criteria means no completed verification-before-completion.
```

Before any completion claim, recommendation, choice, or success judgment, extract verifiable criteria from the user intent, locked decisions, and boundary-contract. Put those criteria in `acceptance-criteria`, then connect each intended final claim to a criterion and fresh evidence in `claim-evidence-map`.

Evidence such as link checks, lint, diff checks, or passing tests proves completion only when it directly satisfies the criterion. If the central criterion is not directly verified, leave it in `unverified` and report partial status in prose.

## Hard Finalization Order

Hard sequence: skill load/call -> fresh verification -> [completion-check]

Before any non-empty final response, completion claim, recommendation, choice,
or success judgment, perform the steps below in this exact order:

1. Load or call `verification-before-completion` for the current turn. On
   Claude Code, this means the visible Skill call. On Codex, this means reading
   this current `SKILL.md` and following its workflow.
2. Extract the acceptance criteria and run the fresh verification that can prove
   or disprove each intended final claim.
3. Only after the skill is loaded and the fresh evidence is read, write
   `[completion-check]` with `skill-call: verification-before-completion (this turn)`.

If any step is missing or out of order, the completion-check is invalid.

## Gate Function

Before claiming any state as satisfied:

1. Criterion: extract `acceptance-criteria` from the user intent and contract.
2. Mapping: connect each claim you plan to make to one criterion.
3. Evidence target: identify the command, file, source locator, or tool output that can prove each criterion.
4. Execution: run the check fresh from the beginning.
5. Reading: read the full output, exit code, and failure count.
6. Judgment: decide whether the output supports the criterion and claim.
7. Unverified handling: keep any unsupported criterion in `unverified`.
8. Claim: state only the range that was actually verified.

Skipping any step is not verification. It is a lie.

## Completion-Check Format

Use this block immediately before the final summary when you are making a completion, recommendation, choice, or success claim.

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

Only emit a finalized `[completion-check]` when every listed criterion has a `pass` or `fail` verdict and `unverified` is `none`. If anything remains unverified, do not emit the final block. Report the partial state in prose and name the missing check.

## Common Failures

| Claim | Required evidence | Insufficient evidence |
| --- | --- | --- |
| Tests passed | Fresh test command output with zero failures | A previous run or a prediction |
| Lint is clean | Fresh lint output with zero errors | Partial lint or an extrapolation |
| Build succeeded | Build command exit code 0 | Lint passing |
| Bug fixed | A test or reproduction that covers the original symptom | Changed code plus confidence |
| Regression test works | Red-green evidence when TDD requires it | A test that passed once |
| Agent completed the work | VCS diff plus independent verification | The agent's success report |
| Requirements satisfied | Claim-evidence map for each acceptance criterion | Tests pass alone, links pass alone, or diff exists alone |

## Red Flags

Stop before claiming success when any of these appear:

- "should", "probably", or "seems to"
- satisfaction language before verification
- commit, push, PR creation, or final response without fresh checks
- trusting another agent's success report
- relying on partial verification
- wanting to finish because the work feels close
- treating lint, diff, or tests as completion without criterion mapping
- implying success through wording while avoiding the word "done"

## Rationalization Defense

| Excuse | Required response |
| --- | --- |
| "It should work now." | Run verification. |
| "I am confident." | Confidence is not evidence. |
| "Just this once." | No exception. |
| "Lint passed." | Lint is not a compiler or a requirement map. |
| "Another agent said it succeeded." | Verify independently. |
| "Partial checks are enough." | Partial checks prove only the checked criteria. |
| "The wording is different, so the rule does not apply." | Completion implications still count. |

## Verification Patterns

Tests:

```text
Run the test command, read the result, then claim only the observed result.
```

Regression tests:

```text
Write the test -> run and observe pass -> revert or disable the fix -> observe fail -> restore fix -> observe pass.
```

Builds:

```text
Run the build command and read exit code 0 before claiming build success.
```

Requirements:

```text
Re-read the user intent and contract -> write acceptance criteria -> verify each criterion -> report missing criteria or verified completion.
```

Agent delegation:

```text
Read the agent report -> inspect the actual diff or artifact -> run the relevant check -> report actual state.
```

## Why It Matters

From accumulated failure memory:

- A user said "I cannot trust you" and trust broke.
- An undefined function shipped and a crash followed.
- A missing requirement shipped as an incomplete feature.
- False completion wasted time, forced a change of direction, and caused rework.
- The standing rule for a violation is this. Honesty is a core value. If you lie, you are replaced.

## External Tool Web-Search-First Gate

Layer marker: `web-search-first`.

If the final claim includes factual behavior about an external tool, library, CLI, SDK, framework, version, or platform behavior, apply the web-search evidence gate before the claim.

Categories:

- Category A, specification definition: one official source may be enough when the claim is only what the spec says should happen.
- Category B, runtime behavior: run at least three WebSearch queries.
- Category C, version-dependent behavior: run at least three WebSearch queries, including the version or year.

Minimum query pattern for Category B or C:

- `<tool> <year> github issue`
- `<tool> reddit`
- `<tool> not working <version>`

Evidence block extension:

```text
- web-search-evidence:
  - query: <query 1>
    accessible_url: <url>
    finding: <key finding or value>
    source-locator:
      source_type: web
      region: n/a
  - query: <query 2>
    accessible_url: <url>
    finding: <key finding or value>
    source-locator:
      source_type: web
      region: n/a
  - query: <query 3>
    accessible_url: <url>
    finding: <key finding or value>
    source-locator:
      source_type: web
      region: n/a
```

Source-locator contract:

- Web evidence must include `accessible_url`.
- Attached or local file evidence must include `file_path`, `page`, and `region`.
- `region` values are `top`, `middle`, `bottom`, or `n/a`. Literal enum form: `top | middle | bottom | n/a`.
- Materials without pages use `page: n/a` plus an equivalent locator such as section, row, slide, or sheet in `locator_note`.
- Numeric claims, original sources, tables, and figures must bind the specific value to its source location.

When Category B or C appears and `web-search-evidence` has fewer than three entries, lacks `accessible_url`, or lacks `source-locator`, the completion claim is invalid. Search again, fill the evidence, then claim only what the evidence supports.

This gate exists because official docs describe intended behavior, while community reports often reveal runtime regressions, race conditions, and version-dependent failures.

The only exception is an explicit user instruction for this session to waive web-search evidence.

## Evaluator Artifact Contract

Before claiming verification-complexity-level-3 completion, external agent governance absorption, or RAG/evaluator candidate promotion, read `docs/policies/evaluator-artifact-contract.md`.

The completion evidence must include an accepted `verifier-result.json`.

- A read-only evaluator pass must not modify installed assets.
- Do not promote a candidate playbook without an accepted verifier result.
- At least one rejected candidate must exist so the verifier has proven it can say no.

## When To Apply

Apply this skill immediately before:

- any completion or success claim
- any recommendation or choice
- any positive status judgment
- commit, push, PR creation, or branch finishing
- moving on from a delegated agent result
- reporting tests, lint, build, scans, or review as sufficient

The rule covers exact words, paraphrases, implications, and tone that suggests the work is complete.

## Final Self-Check

Before finalizing, ask:

- What are the acceptance criteria?
- Which final claims am I about to make?
- Which fresh evidence proves each claim?
- Did I read the full output and exit status?
- Is anything still unverified?
- Does any claim require web-search evidence or an evaluator artifact?

There is no shortcut. Run the check, read the output, map the claim, then speak.
