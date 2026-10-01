---
name: verification-before-completion
description: Use before claiming completion, fixes, passes, commits, or PR creation, and before re-reading an artifact to support a claim. Evidence always comes before claims; reuse unchanged evidence and check only when the result can change.
compatibility:
  - "Python 3.11+ standard library"
---

# Verification Before Completion
## Contents

- [Overview](#overview)
- [Iron Law](#iron-law)
- [Acceptance Criteria Iron Law](#acceptance-criteria-iron-law)
- [Relayed Verdicts And Absence Claims](#relayed-verdicts-and-absence-claims)
- [Verify-Or-Reuse Gate](#verify-or-reuse-gate)
- [Hard Finalization Order](#hard-finalization-order)
- [Autopilot Proof Publication](#autopilot-proof-publication)
- [Retain Evidence On First Execution](#retain-evidence-on-first-execution)
- [Gate Function](#gate-function)
- [Evidence Selection And Stop Gate](#evidence-selection-and-stop-gate)
- [Completion-Check Format](#completion-check-format)
- [Confirmed Flaw Propagation](#confirmed-flaw-propagation)
- [Common Failures](#common-failures)
- [Red Flags](#red-flags)
- [Rationalization Defense](#rationalization-defense)
- [Why It Matters](#why-it-matters)
- [External Tool Web-Search-First Gate](#external-tool-web-search-first-gate)
- [Evaluator Artifact Contract](#evaluator-artifact-contract)
- [When To Apply](#when-to-apply)
- [Final Self-Check](#final-self-check)


## Overview

A new closure claim without decision-relevant fresh evidence is not efficiency. It is a lie.

Core principles:

- Evidence always comes before the claim.
- The letter of the rule and the spirit of the rule are the same rule.
- A surrounding signal is not direct proof unless it satisfies the relevant criterion.

## Iron Law

```text
Do not claim a new current-turn closure without decision-relevant fresh verification from this turn.
```

Explaining unchanged prior work is not a new closure claim. Cite the existing evidence and its age instead of rerunning unchanged work merely because another message arrived.

Every user input reopens routing; it does not by itself invalidate unchanged evidence or require reverification. Reverify when the relevant state, artifact, or criterion changed; a new error, mismatch, contradiction, or instability appeared; or the user explicitly requested a new check. A change is an observed mutation event, not a possibility, a storage location, or a new user message.

If a verification command or inspection did not run in this message, do not claim that it freshly passed in this message.

## Acceptance Criteria Iron Law

```text
No acceptance-criteria means no completed verification-before-completion.
```

Before any claim that executed work is complete, fixed, successful, or freshly verified, extract verifiable criteria from the user intent, locked decisions, and boundary-contract. Put those criteria in `acceptance-criteria`, then connect each intended closure claim to a criterion and fresh evidence in `claim-evidence-map`.

Keep the requested result as the terminal criterion. If the user requested implementation, a compatibility report, successful diagnostic, observation record, or list of remaining gaps is supporting work. Do not replace the implementation criterion with a report criterion, narrow it to the subset that passed, or mark the task complete because the supporting work is verified. A report is the deliverable only when the user requested a report.

Evidence such as link checks, lint, diff checks, or passing tests proves completion only when it directly satisfies the criterion. If the central criterion is not directly verified, leave it in `unverified` and report partial status in prose.

When aggregating repair status, preserve each item's evidence scope before composing the lead or table: the target and failure context checked, the kind of verification, and the conclusion it supports. Reuse the existing records; this mapping does not require a new artifact or another run. Code regression, instruction conformance, installed delivery, and behavior in a continuing conversation establish different things. Edited and installed guidance alone is not a demonstrated behavioral repair. A short isolated decision does not establish recovery in an accumulated context it did not exercise.

Scope the lead and each row to that evidence. Do not flatten unlike stages into "all fixed and verified" and try to repair the implication with a trailing "no guarantee in every situation" disclaimer. If the old and new guidance both pass a case, it supports that checked behavior, not a causal improvement; missing improvement evidence is not merely an unmeasured percentage. Keep a verified source repair or installation complete at its own stage while naming the specific unsupported behavioral conclusion.

Honor the user's agreed disposition criteria, including treating a historical issue as handled when the selected current check does not reproduce it. Do not replace that agreement with a universal-reliability requirement, reopen handled cases, or rerun unchanged work. Apply the scope mapping to the completion criterion actually agreed.

While actionable authorized work remains, that partial status is a commentary checkpoint followed by the next supported action. A failed provider branch or bookkeeping conflict preserves its specific gap; it does not finish independent implementation. Stop or yield when the user explicitly requests it, when a required decision or authorization is missing, or when no supported work remains. A failed criterion proves the failure, not successful task completion.

## Relayed Verdicts And Absence Claims

A verdict you endorse as current is your own claim. Put it in the `claim-evidence-map`. If verify-or-reuse finds an observed mutation event or an evidenced open change path for a current-state claim, gather fresh evidence; inheriting a source's verdict is not evidence. If the task is only to explain an unchanged prior result, cite the existing evidence and its age without recreating it. Severity does not lower the bar.

An absence claim -- "no test exists", "X is not enforced", "nothing handles this" -- is never proven by reasoning or by a source's say-so. For a new absence claim, or one whose searched state has an observed mutation event, use a targeted current search that would surface the thing if present. Reuse a relevant prior search only when the searched state and criterion are unchanged, and state its age.

A verdict stated only in prose, outside the `claim-evidence-map`, escapes this gate. If you assert it, map it.

## Verify-Or-Reuse Gate

Before re-reading an artifact to support a claim, apply verify-or-reuse: reuse retained evidence unless an observed trigger exists.

This gate applies to questions as well as closure claims. The flow, vocabulary, and decision table live in `references/verify-or-reuse.md`, with `scripts/verify_or_reuse.py` as its executable form; resolve both relative to this skill directory on every platform.

- A trigger is an explicit re-inspection request, an observed mutation event after the evidence, an evidenced externally writable artifact for a current-state claim, lost retained evidence, or evidence that covers a different copy.
- An as-authored claim is answered from retained authored content, even if the artifact changed later.
- An unknown mutation authority does not open a change path. Answer a current-state claim as the last known state and say so instead of re-reading.
- Check the copy the claim is about; a proxy copy is `wrong-copy`. A user designation or dated or compared content selects among versions; a folder name or familiarity does not.
- When the writer confirms nothing relevant changed, reuse the retained evidence as current unless an observed mutation event contradicts it.
- When a trigger exists, run one minimal check of the target copy and keep its result. Do not repeat it without a new trigger.

Answer an objection that a check was redundant from the recorded mutation authority and events, not from hypothetical external changes.

Say each fact, status, plan, and apology once; later messages carry only new results or decisions.

## Hard Finalization Order

Hard sequence for a new current-turn closure claim: skill load/call -> decision-relevant fresh verification -> [completion-check]. A successful result already returned in this turn, such as a write or a test run, is that fresh verification; the order places the skill before the claim and does not require repeating a successful check.

Before any executed-work completion, fix, success, or fresh-verification claim, perform the steps below in this exact order:

1. Load or call `verification-before-completion` for the current turn. On Claude Code, this means the visible Skill call. On Codex, this means reading this current `SKILL.md` and following its workflow.
2. Extract the acceptance criteria and map each intended final claim to fresh evidence from this turn: cite a successful result already returned, such as a write or a test run, and run a decision-relevant check only for a claim that no returned result covers.
3. Only after the skill is loaded and every claim has its evidence, write `[completion-check]`; on Codex, include the `skill-call: verification-before-completion (this turn)` line.

If any step is missing or out of order, the completion-check is invalid.

## Autopilot Proof Publication

When this Codex or Claude installation includes `autopilot-mode/scripts/autopilot_completion.py` and the current session has admitted criteria for authorized execution, read [references/autopilot-publication.md](references/autopilot-publication.md) before preparation, business verification or the first final answer. Follow the current hook notice, exact receipt coordinates and original evidence times; publication remains required before the initial final answer. Plan-only replies, explanations, installations without this helper and sessions without admitted execution criteria do not activate this path. A binding or publication failure does not erase supported business evidence or finish independent authorized work. Do not create criteria to activate publication, replace foreign run state or rerun unchanged checks for bookkeeping.

## Retain Evidence On First Execution

Before running a necessary check or inspection, decide how to retain its returned output, exit status, source locator and actual verification time. In an orchestrator call, keep the original result in session state before returning it when later steps need that object. This avoids losing a successful result at an execution boundary.

An already returned successful tool result is evidence. Cite its existing locator or copy its unchanged contents into permitted scratch if persistence is needed. Do not rerun the same successful inspection merely to populate `store`, add redirection, create an evidence file, recover an execution-local variable, or satisfy publication bookkeeping. Missing storage is not missing verification. Rerun only when the original result is unavailable or incomplete, the relevant state changed, or a decision-relevant contradiction requires it; state that reason before running.

Preserve the original source and time when copying evidence. Do not manufacture a fresh verification timestamp while saving it. If the source or original time cannot be established, report that specific evidence gap rather than relabeling an old result. Publication remains required before the first final answer for the admitted execution path; retaining evidence does not replace publication or establish a verdict.

## Gate Function

Before claiming any state as satisfied:

1. Criterion: extract `acceptance-criteria` from the user intent and contract.
2. Mapping: connect each claim you plan to make to one criterion.
3. Uncertainty: name the live uncertainty and the next decision each possible outcome can change.
4. Evidence target: identify the command, file, source locator, or tool output that can prove each criterion.
5. Execution: run the smallest decision-relevant check when the uncertainty gate requires fresh evidence.
6. Reading: read the full output, exit code, and failure count.
7. Judgment: decide whether the output supports the criterion and claim.
8. Unverified handling: keep any unsupported criterion in `unverified`.
9. Claim: state only the range that was actually verified.

Skipping any step is not verification. It is a lie.

## Evidence Selection And Stop Gate

Current accessible behavior or content is the default direct evidence for semantic claims. That default governs which evidence a required check uses, not whether to check: while verify-or-reuse returns reuse, retained authored or inspected evidence remains direct evidence. Hash or provenance evidence is appropriate when the criterion is artifact identity, integrity, drift, merge safety, or reproducibility. Do not use hash equality, byte identity, cache history, or repository lineage as a proxy for current semantic behavior.

Before running a check, name the live uncertainty and the next decision that each possible outcome can change. If no possible outcome can change the criterion or next decision, do not run the check.

Verification output does not create a new obligation to verify the verification. Stop when a repeated check produces no relevant state delta, or when further checking would displace the user's primary objective. Resume only after a state change, new error, mismatch, contradiction, instability, or explicit request.

## Completion-Check Format

Use this block immediately before the final summary when you are making an executed-work completion, fix, success, or fresh-verification claim.

```text
[completion-check]
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

On Codex, where no visible Skill tool exists, start the block with `- verification-before-completion: done` and `- skill-call: verification-before-completion (this turn)`, only after that skill's `SKILL.md` was read and followed in the current turn. On Claude Code, omit both lines: the Stop hook verifies the visible Skill call from the transcript.

Serialize `claim`, `criterion`, `evidence`, and `verdict` on their own physical lines. Emit an evidence-supported bare `pass` or `fail` verdict with no trailing punctuation, quotes, markup, or explanatory prose. If evidence does not support a verdict, report honest partial state without a finalized `[completion-check]`. Record the actually called `verification-before-completion` skill in an explicit `skills-loaded` list in `[io-trace]`. A format repair must preserve the substantive business result and supported evidence.

Only emit a finalized `[completion-check]` when every listed criterion has a `pass` or `fail` verdict and `unverified` is `none`. If anything remains unverified, do not emit the final block. Report the partial state in prose and name the missing check.

## Confirmed Flaw Propagation

When you confirm a flaw in a prompt or other deliverable you provided, return the corrected complete deliverable unless the user asked only for diagnosis (`confirmed-flaw-not-propagated`).

- The confirmed flaw is a failed criterion for that deliverable until the corrected version is delivered.
- Do not return the previous version again after the flaw is confirmed.
- A safe fix inside the existing scope needs no renewed approval. Ask first only when the fix is unsafe or outside the agreed scope.

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
| Requested implementation complete | Implemented behavior and its original-failure regression | A verified report of compatibility, observations, or remaining work |
| Relayed/endorsed review verdict | Current behavior evidence when an observed mutation event or evidenced open change path affects it; otherwise the relevant existing evidence with its age | The reviewer's verdict, or your agreement with it, alone |
| Absence claim ("no test/code exists", "not enforced") | A targeted current search when an observed change could alter it; otherwise the relevant existing search with its age | Reasoning or the source's say-so |
| Content of an artifact you authored and still retain | Retained authored evidence when verify-or-reuse returns reuse | A re-read justified only by a possible change |
| Deliverable after a confirmed flaw | The corrected complete deliverable | An explanation of the flaw alone |

## Red Flags

Stop before claiming success when any of these appear:

- "should", "probably", or "seems to"
- satisfaction language before verification
- a new closure, commit, push, or PR claim without decision-relevant checks
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
| "Another agent said it succeeded." | Endorse it only with decision-relevant evidence; reuse unchanged evidence with its age. |
| "Partial checks are enough." | Partial checks prove only the checked criteria. |
| "I reported the gaps, so the implementation task is complete." | Keep the requested result as the criterion and continue supported authorized work. |
| "The wording is different, so the rule does not apply." | Completion implications still count. |
| "It might have been edited externally." | Name the actual change path and observed event, or answer from retained evidence. |

Verification patterns for tests, regressions, builds, requirements, and delegation: `references/verification-detail.md`.

## Why It Matters

From accumulated failure memory:

- A user said "I cannot trust you" and trust broke.
- An undefined function shipped and a crash followed.
- A missing requirement shipped as an incomplete feature.
- False completion wasted time, forced a change of direction, and caused rework.
- The standing rule for a violation is this. Honesty is a core value. If you lie, you are replaced.

## External Tool Web-Search-First Gate

Layer marker: `web-search-first`. Before a material factual claim about an external tool, library, CLI, SDK, framework, version or platform behavior, read [references/external-tool-evidence.md](references/external-tool-evidence.md) and apply its evidence gate. Category A specification definitions may use one official source; Category B runtime behavior and Category C version-dependent behavior require at least three WebSearch queries and accessible `source-locator` values. Keep the explicit user waiver and the project's stable, low-risk direct-response exemption; do not manufacture external claims or searches for a local-only task.

## Evaluator Artifact Contract

Before claiming verification-complexity-level-3 completion, external agent governance absorption or RAG/evaluator candidate promotion, read [references/evaluator-artifacts.md](references/evaluator-artifacts.md) and `docs/policies/evaluator-artifact-contract.md`. Require an accepted `verifier-result.json` with a rejected candidate; stop promotion when it is absent or rejected. A read-only evaluator must not modify installed assets.

## When To Apply

Apply this skill immediately before:

- any completion or success claim
- any recommendation or choice that claims finished work or verified results
- any new current-turn positive status judgment
- commit, push, PR creation, or branch finishing
- endorsing a delegated agent result as current after an observed change to the relevant state
- reporting tests, lint, build, scans, or review as sufficient
- re-reading, fetching, or re-inspecting an artifact to answer a question or support a claim
- confirming a flaw in a deliverable you provided

The rule covers exact words, paraphrases, implications, and tone that suggests the work is complete.

## Final Self-Check

Before finalizing, ask:

- What are the acceptance criteria?
- Which closure claims am I about to make?
- Does each new closure claim require fresh evidence, or is relevant unchanged evidence sufficient?
- What live uncertainty and next decision can the check change?
- Before any re-read, fetch, or re-inspection, did verify-or-reuse name an observed trigger?
- Did a confirmed flaw in my own deliverable produce the corrected complete deliverable?
- Did I read the full output and exit status?
- Is anything still unverified?
- Does any claim require web-search evidence or an evaluator artifact?

Map the claim, apply the uncertainty gate, run a check only when its outcomes can change the decision, read the output, then speak.
