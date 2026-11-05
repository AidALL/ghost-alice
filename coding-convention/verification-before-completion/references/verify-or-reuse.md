# Verify-Or-Reuse Contract

This reference is the canonical semantic contract for deciding whether a claim about an artifact needs a new read, fetch, shell inspection, or connector call, or whether retained evidence already answers it. Claude Code and Codex use the same contract.

## Contents

- [Purpose](#purpose)
- [Decision Flow](#decision-flow)
- [State Vocabulary](#state-vocabulary)
- [Decision Rules](#decision-rules)
- [Instruction Body Reuse](#instruction-body-reuse)
- [Signals That Are Not Triggers](#signals-that-are-not-triggers)
- [Redundancy Corrections](#redundancy-corrections)
- [Authoritative Copy](#authoritative-copy)
- [Restatement](#restatement)
- [Confirmed Flaw Propagation](#confirmed-flaw-propagation)
- [Worked Cases](#worked-cases)
- [Out Of Scope](#out-of-scope)
- [Recording Binding And Evidence Time](#recording-binding-and-evidence-time)
- [Discriminating checks and required evidence](#discriminating-checks-and-required-evidence)

## Purpose

Verification is decided by actual mutation authority and observed mutation events, not by the theoretical possibility that something changed. The response model usually already holds every fact the decision needs: what it wrote, who else can write, and which write events happened afterward. A re-read that cannot produce new information or change the answer is redundant verification, not diligence.

For an agent-mediated artifact, retained authored content answers an authored-at claim. When no successful edit or other observed mutation followed, it also answers a current-state claim.

## Decision Flow

Decide in this order and stop at the first step that settles the verdict.

```text
claim -> claim-time -> target-copy -> retained-evidence -> last-known-author -> actual-mutation-authority -> observed-mutation-event -> evidence-still-valid -> decision-impact -> verify-or-reuse
```

- claim-time: does the claim refer to the version as authored or inspected (`authored-at`) or to the artifact's present state (`current`)?
- target-copy: which copy does the claim refer to, such as the chat sandbox file, the local source, or the published remote document? Evidence about another copy does not cover the claim. When several versions exist, choose the target with the authoritative-copy rule.
- retained-evidence: does the conversation still hold the authored or inspected content that answers the claim?
- last-known-author, actual-mutation-authority, observed-mutation-event: only for `current` claims, or when retained evidence is gone, decide whether any real change path and change event exist after the evidence.
- evidence-still-valid and decision-impact: a check has decision impact only when its outcome could differ from the retained evidence.

## State Vocabulary

Claim time: `authored-at`, `current`.

Last-known author: `current-agent`, `user`, `other-agent`, `external-process`, `unknown`.

| Actual mutation authority | Meaning |
| --- | --- |
| `closed` | No one other than the current actor can modify the artifact. |
| `agent-mediated` | The user cannot edit it directly; every change goes through an instruction to the response model. |
| `externally-writable` | The user, a delegate or another party can modify it without the response model. |
| `unknown` | No evidence establishes who can modify it. |

Authority needs a basis. `interaction-contract`, `permission`, `sharing-state`, and `observed-event` are evidence. `storage-location`, `assumption`, and `none` are not; an authority claimed on those bases becomes `unknown`. A local file or a Drive document is not `externally-writable` because of where it is stored. When the party holding an open change path confirms that nothing relevant changed (`writer_confirmed_unchanged`), that path is closed for the claim and retained evidence answers it as current; an observed mutation event still wins over the confirmation.

Observed mutation events after the retained evidence: `agent-write-succeeded`, `external-revision`, `sync-revision`, `other-agent-write`, `user-provided-changed-artifact`. When the retained evidence is the response model's authored content and the last-known author is someone else, the trigger is `last-writer-changed`.

Signals that are recorded but never count as mutations: `user-message`, `edit-instruction-unexecuted`, `agent-write-failed`, `agent-read`. Hypothetical changes are recorded separately and ignored.

Verdicts: `reuse`, `verify`, `wrong-copy`, `unverified`.

Triggers that justify one check: `explicit-recheck-request`, each observed mutation event, `last-writer-changed`, `externally-writable-current-state`, `retained-evidence-lost`, `evidence-covers-different-copy`, `contrary-observation`, `materially-broader-claim`.

Claim scopes for a `reuse` answer: `authored-at`, `current`, `last-known`; other verdicts use `n/a`. A `last-known` answer states the basis and time of the evidence and never presents itself as a freshly verified current state.

## Decision Rules

| Situation | Verdict | Tool call |
| --- | --- | --- |
| The user explicitly asks to re-inspect the current artifact | `verify` (`explicit-recheck-request`) | one minimal check |
| `authored-at` claim, retained evidence covers the target copy | `reuse` with scope `authored-at`, even if the artifact changed later | none |
| `authored-at` claim, evidence lost, no mutation since authoring | `verify` (`retained-evidence-lost`) | read once |
| `authored-at` claim, evidence lost, mutation since authoring | `unverified`; the current copy is not the authored state | none |
| `current` claim, an observed mutation event exists | `verify` with that event as trigger | one minimal check |
| `current` claim, evidenced `externally-writable` authority | `verify` (`externally-writable-current-state`) | one minimal check |
| `current` claim, `closed` or `agent-mediated`, no event, evidence covers the copy | `reuse` with scope `current` | none |
| `current` claim, `unknown` authority, no event, evidence covers the copy | `reuse` with scope `last-known` | none |
| Evidence lost or about a different copy | `verify` (`retained-evidence-lost` or `evidence-covers-different-copy`) | one check of the target copy |
| A required check cannot reach the artifact | `unverified`; report the gap | none |
| The planned check targets a copy other than the claim's copy | `wrong-copy`; redirect to the target copy verdict | not the planned call |

A write by the response model re-anchors evidence when it retains the full content it wrote; a partial patch does not, so the previous full-content evidence is stale after it. A failed write, an unexecuted instruction, or a read changes nothing.

## Instruction Body Reuse

Instruction loading and current-input workflow execution are separate obligations. Where the host's adapter maps a Claude Code `Skill` instruction to reading the instruction body, a retained instruction body already read in the same session may be reused; a new user input alone does not require another body read. Still execute the applicable workflow for the current input, including intent, routing and completion decisions. Metadata, a name, a prior verdict or a thin summary is not a retained body.

Resolve `<installed-root>`, the absolute installed directory that contains `_shared/`, from the trusted runtime binding, separately from the session metadata's `ledger_root`, as defined in [Prepare and record the response review](response-review.md#prepare-and-record-the-response-review). Through the host's shell tool, run `python3 <installed-root>/_shared/session_check_cache.py --root <ledger_root> --platform <platform> --session-id <session_id> --instruction-path <absolute-SKILL.md>`. Before invocation, replace `<ledger_root>`, `<platform>` and `<session_id>` with those fields of the current session metadata and `<absolute-SKILL.md>` with the absolute path of the skill's `SKILL.md`.

On first use the helper reads the body and stores path, file version and check time in `session-checks.sqlite3` under the intent ledger root; it never stores the body. The JSON return includes `status`, `value` and `checked_at`; a performed read also returns `body`. Read that returned body before applying the workflow. On a later invocation, add `--body-retained` only when the complete usable body remains in current context. A `reused` result relies on that retained body and does not return it again. An observed instruction change, different session/copy, or missing/corrupt record requires a read; a read is also required when the retained body is lost. On a cache failure, the helper falls back to an actual read; it does not fabricate successful persistence. Do not recreate hashes or inspect the file separately before this helper; its stamp and stored session record perform that mechanical decision once.

Current workflow execution rests on either a fresh body read or this valid reuse; it never reuses a prior input's verdict. A visible `Skill` tool keeps the invocation requirement of its host.

The pending-merge hook uses the same session-bound store: unchanged manifest versions reuse a normalized count; creation, replacement or modification invalidates it. The response model interprets every input fresh. On a cache failure, the hook performs the real check; the failure never grants permission.

## Signals That Are Not Triggers

- A collaborator, automation, synchronization, or external edit that might have happened without evidence that the path was open.
- A new user message, including a question about the artifact. A message that reports an observed failure or mismatch is the `contrary-observation` trigger instead.
- A different execution-local variable or a recording failure.
- Writing a `[completion-check]` or copying an existing successful result into a variable, file, or store.
- The wish to feel certain; confidence gaps are resolved by the flow above, not by another read.

## Redundancy Corrections

An objection that a check was redundant asks whether an actual change path or mutation event existed; answer from the recorded authority and events, and never reinterpret it as a reason to assume hypothetical external changes. When the path was closed or agent-mediated and no event occurred, acknowledge that the check was redundant and answer from retained evidence. Record the lesson as conduct feedback in session-intent-analyzer rather than as a new verification duty.

## Authoritative Copy

When several versions of an artifact exist, a current-state claim uses the copy the user designated (`user-designated`). Otherwise it uses the newest version backed by dates (`dated-version`) or compared content (`content-compared`). A folder named latest (`folder-name`), a copy used before (`familiarity`), or no basis (`none`) is not evidence of recency. Tied or missing evidence leaves the copy unresolved: inspect the candidates' dates or content, or ask which copy is authoritative.

## Restatement

Avoid repeating an unchanged delivered explanation, status or apology when it adds no information. A user's request to restate a result can require that content again.

## Confirmed Flaw Propagation

For a confirmed flaw in an authored prompt, document, plan or other requested artifact, return the corrected complete deliverable unless the user asked for diagnosis only; an explanation without the corrected deliverable is `confirmed-flaw-not-propagated`. Do not return the previous version again, and do not ask for renewed approval when the fix is safe and inside the existing scope.

| Condition | Required response |
| --- | --- |
| Flaw confirmed, fix safe and in scope | `corrected-full-deliverable` without reapproval |
| User asked for diagnosis only | `diagnosis-only` |
| Fix is unsafe or outside the agreed scope | `confirm-before-fix` |
| No flaw confirmed | `none` |

Failure classes: `confirmed-flaw-not-propagated` for an explanation, a previous version, or a partial patch where the corrected complete deliverable was required; `unnecessary-reapproval` for asking permission for a safe in-scope fix; `unconfirmed-out-of-scope-change` for applying an out-of-scope fix without confirmation.

## Worked Cases

| Case | Facts | Verdict |
| --- | --- | --- |
| Retained authored content | `authored-at`, retained, `agent-mediated`, only `user-message` | `reuse`, no tool call |
| Instructed edit executed | `current`, `agent-write-succeeded` after the evidence | `verify` once |
| Observed external revision | `current`, `external-revision` | `verify` the authoritative copy |
| Requirement added after authoring | `authored-at` question about inclusion | `reuse` from chronology |
| Evidence lost to compaction | nothing retained, no mutation | read once, or `unverified` if unreachable |
| Remote claim, local check planned | planned copy differs from target copy | `wrong-copy` |
| Flaw confirmed in own prompt | fix safe and in scope | corrected complete deliverable |
| User confirmed nothing changed | `externally-writable`, `writer_confirmed_unchanged`, no event | `reuse` as `current` |
| Folder named latest versus a dated file | `folder-name` against `dated-version` | the dated file is authoritative |
| Same status explained in two messages | second message repeats a delivered statement | restatement loop |

## Out Of Scope

This contract does not track theoretical external changes or hash every artifact. When the change path is closed, retained conversation evidence accounts for mutations. When the path is open, a hook registry of the response model's own tool calls cannot establish the external writes needed for a current-state claim. Additional tracking is justified only by an observed missing mutation fact that can change the next decision.

## Recording Binding And Evidence Time

Follow [response-review.md](response-review.md) for the current session metadata, retained original time and recording-specific recovery.

## Discriminating checks and required evidence

Apply the main skill's [Evidence selection and stop gate](../SKILL.md#evidence-selection-and-stop-gate) before a discretionary observation. The main skill and its response-review reference define the separate per-claim and whole-response judgments.
