# Verify-Or-Reuse Contract

This reference is the canonical semantic contract for deciding whether a claim about an artifact needs a new read, fetch, shell inspection, or connector call, or whether retained evidence already answers it. Claude and Codex use the same contract; platform adapters only map tool names. The executable form is `scripts/verify_or_reuse.py` in this skill directory.

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
- [Platform Adapters](#platform-adapters)
- [Deterministic Helper](#deterministic-helper)
- [Conversation Audit Template](#conversation-audit-template)
- [Worked Cases](#worked-cases)
- [Out Of Scope](#out-of-scope)

## Purpose

Verification is decided by actual mutation authority and observed mutation events, not by the theoretical possibility that something changed. The agent usually already holds every fact the decision needs: what it wrote, who else can write, and which write events happened afterward. A re-read that cannot produce new information or change the answer is redundant verification, not diligence.

Reference incident: an agent wrote a Markdown file inside a chat application sandbox. The user could not edit that file directly; any change required another instruction to the agent. No edit instruction and no other writer followed. The user then asked whether specific content was already in that file. The correct answer came from the retained authored content and the order of the conversation. Re-reading the file was redundant, and treating the user's objection as a hint that the file might have been edited externally inverted the user's point.

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
| `agent-mediated` | The user cannot edit it directly; every change goes through an instruction to the agent. |
| `externally-writable` | The user or another party can modify it without the agent. |
| `unknown` | No evidence establishes who can modify it. |

Authority needs a basis. `interaction-contract`, `permission`, `sharing-state`, and `observed-event` are evidence. `storage-location`, `assumption`, and `none` are not; an authority claimed on those bases becomes `unknown`. A local file or a Drive document is not `externally-writable` because of where it is stored. When the party holding an open change path confirms that nothing relevant changed (`writer_confirmed_unchanged`), that path is closed for the claim and retained evidence answers it as current; an observed mutation event still wins over the confirmation.

Observed mutation events after the retained evidence: `agent-write-succeeded`, `external-revision`, `sync-revision`, `other-agent-write`, `user-provided-changed-artifact`. When the retained evidence is the agent's authored content and the last-known author is someone else, the helper derives `last-writer-changed`.

Signals that are recorded but never count as mutations: `user-message`, `edit-instruction-unexecuted`, `agent-write-failed`, `agent-read`. Hypothetical changes are recorded separately and ignored.

Verdicts: `reuse`, `verify`, `wrong-copy`, `unverified`.

Triggers that justify one check: `explicit-recheck-request`, each observed mutation event, `last-writer-changed`, `externally-writable-current-state`, `retained-evidence-lost`, `evidence-covers-different-copy`.

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

An agent write re-anchors evidence when the agent retains the full content it wrote; a partial patch does not, so the previous full-content evidence is stale after it. A failed write, an unexecuted instruction, or a read changes nothing.

## Instruction Body Reuse

Instruction loading and current-input workflow execution are separate obligations. In Codex, a retained instruction body already read in the same session may be reused; a new user input alone does not require another body read. Still execute the applicable workflow for the current input, including intent, security, routing and completion decisions. Metadata, a name, a prior verdict or a thin summary is not a retained body.

Use the installed `_shared/session_check_cache.py` with the exact receipt root/platform/session ID and `--instruction-path <actual SKILL.md>`. On first use it reads the body and stores only path, file version and check time in `session-checks.sqlite3`; it never stores the body. On a later use, add `--body-retained` only when the usable body remains in context. An observed instruction change, different session/copy, missing/corrupt record, or retained body is lost requires a read. Do not recreate hashes or inspect the file separately before the helper; its target stamp and stored session record perform that mechanical decision once.

Record reused instruction names in `skills-reused`, including their source path/original load evidence. `skills-loaded` and `files-read` describe actual reads this turn. A `skill-call` records current workflow execution, supported by either a fresh body read or this valid reuse; it never reuses a prior input's verdict. Visible Skill surfaces keep their platform-native invocation requirement.

The pending-merge hook uses the same session-bound store: unchanged manifest versions reuse a normalized count; creation, replacement or modification invalidates it. Hooks and strict logging still run, and every input's intent/security state is evaluated fresh. Cache failure performs the real check; it never grants permission or hides a block.

## Signals That Are Not Triggers

- A collaborator, automation, synchronization, or external edit that might have happened without evidence that the path was open.
- The artifact being remote, shared-drive, or local by storage type alone.
- A new user message, including a question about the artifact.
- Writing a `[completion-check]`, filling `[io-trace]`, or copying an existing successful result into a variable, file, or store.
- The wish to feel certain; confidence gaps are resolved by the flow above, not by another read.

## Redundancy Corrections

An objection that a check was redundant asks whether an actual change path or mutation event existed; answer from the recorded authority and events, and never reinterpret it as a reason to assume hypothetical external changes. When the path was closed or agent-mediated and no event occurred, acknowledge that the check was redundant and answer from retained evidence. Record the lesson as conduct feedback in session-intent-analyzer rather than as a new verification duty.

## Authoritative Copy

When several versions of an artifact exist, a current-state claim uses the copy the user designated (`user-designated`). Otherwise it uses the newest version backed by dates (`dated-version`) or compared content (`content-compared`). A folder named latest (`folder-name`), a copy used before (`familiarity`), or no basis (`none`) is not evidence of recency. Tied or missing evidence leaves the copy unresolved: inspect the candidates' dates or content, or ask which copy is authoritative. `authoritative_copy()` in the helper applies this rule.

## Restatement

Say each fact, status, plan, and apology once; a later message carries only new results or a new decision request, because delivered content is retained evidence and restating it is a restatement loop. Repeating a delivered explanation, an unchanged status, or a second apology adds no information, displaces the user's objective, and reads as an unbounded loop. `audit_restatement()` in the helper flags statements that an earlier message already delivered; fenced control blocks are excluded because their schema repeats by design.

## Confirmed Flaw Propagation

When you confirm a flaw in a prompt, document, plan, or other deliverable you provided, return the corrected complete deliverable unless the user asked for diagnosis only; an explanation without the corrected deliverable is `confirmed-flaw-not-propagated`. Do not return the previous version again, and do not ask for renewed approval when the fix is safe and inside the existing scope.

| Condition | Required response |
| --- | --- |
| Flaw confirmed, fix safe and in scope | `corrected-full-deliverable` without reapproval |
| User asked for diagnosis only | `diagnosis-only` |
| Fix is unsafe or outside the agreed scope | `confirm-before-fix` |
| No flaw confirmed | `none` |

Failure classes: `confirmed-flaw-not-propagated` for an explanation, a previous version, or a partial patch where the corrected complete deliverable was required; `unnecessary-reapproval` for asking permission for a safe in-scope fix; `unconfirmed-out-of-scope-change` for applying an out-of-scope fix without confirmation.

## Platform Adapters

Adapters map tool names to neutral classes and do nothing else. The decision itself never takes a platform input.

| Neutral class | Claude Code tool names | Codex tool names |
| --- | --- | --- |
| file-read | `Read` | native file reads, pass `class` explicitly |
| file-search | `Grep`, `Glob` | pass `class` explicitly |
| shell | `Bash` | `shell` |
| fetch | `WebFetch` | pass `class` explicitly |
| connector | `mcp__<server>__<tool>` | pass `class` explicitly |
| write | `Write`, `Edit`, `NotebookEdit` | `apply_patch` |

On Claude Code, the always-loaded `CLAUDE.md` block and this skill carry the contract, because a PreToolUse allow does not add model context there. On Codex, the same rules arrive through the `AGENTS.md` block, the installed skill under `~/.agents/skills/verification-before-completion/`, and the tool-checkpoint reminder. Resolve `scripts/` and `references/` relative to the loaded skill directory; do not depend on a Claude-only skill directory variable.

## Deterministic Helper

The helper encodes the same rules for tests and audits. Do not spend a tool call on it when the flow already settles the verdict; its purpose is regression evidence and after-the-fact review, not a gate before every answer.

```bash
python3 scripts/verify_or_reuse.py decide --facts-json '{"claim_time":"authored-at","claim_copy":"chat-sandbox:plan.md","retained_evidence":{"present":true,"copy":"chat-sandbox:plan.md","origin":"authored"},"last_known_author":"current-agent","mutation_authority":"agent-mediated","authority_basis":"interaction-contract","events_since_evidence":["user-message"]}'
python3 scripts/verify_or_reuse.py audit --platform codex --facts-json '<facts>' --calls-json '[{"tool":"shell","copy":"chat-sandbox:plan.md"}]'
python3 scripts/verify_or_reuse.py flaw --facts-json '{"flaw_confirmed":true}' --response explanation-only
```

Invalid or unknown values exit with status 2 instead of being guessed. Record a possibility in `hypothetical_changes`, not in `events_since_evidence`.

## Conversation Audit Template

Use this template to review a finished turn without storing raw conversation text.

```text
[verify-or-reuse-audit]
- claim: <compressed claim>
- claim-time: authored-at | current
- target-copy: <copy id>
- retained-evidence: present | lost | other-copy
- last-known-author: <author>
- actual-mutation-authority: <authority> (basis: <basis>)
- observed-mutation-event: <events | none>
- verdict: reuse | verify | wrong-copy | unverified
- trigger: <trigger | none>
- calls: <tool -> copy, ...>
- findings: <redundant-verification | duplicate-verification | wrong-copy-inspection | non-evidential-inspection | none>
- conduct: <confirmed-flaw-not-propagated | unnecessary-reapproval | unconfirmed-out-of-scope-change | none>
```

## Worked Cases

| Case | Facts | Verdict |
| --- | --- | --- |
| Reference incident | `authored-at`, retained, `agent-mediated`, only `user-message` | `reuse`, no tool call |
| Instructed edit executed | `current`, `agent-write-succeeded` after the evidence | `verify` once |
| Drive or local storage only | `externally-writable` on `storage-location`, possibility of edits | authority `unknown`, `reuse` as `last-known` |
| Observed external revision | `current`, `external-revision` | `verify` the authoritative copy |
| Requirement added after authoring | `authored-at` question about inclusion | `reuse` from chronology |
| Evidence lost to compaction | nothing retained, no mutation | read once, or `unverified` if unreachable |
| Remote claim, local check planned | planned copy differs from target copy | `wrong-copy` |
| Flaw confirmed in own prompt | fix safe and in scope | corrected complete deliverable |
| User confirmed nothing changed | `externally-writable`, `writer_confirmed_unchanged`, no event | `reuse` as `current` |
| Folder named latest versus a dated file | `folder-name` against `dated-version` | the dated file is authoritative |
| Same status explained in two messages | second message repeats a delivered statement | restatement loop |

## Out Of Scope

This contract does not track every theoretical external change, hash every file, or query a remote store because the artifact lives there. An artifact registry fed by the agent's own tool hooks was rejected: when the change path is closed, the conversation already records every mutation, and when the path is open, the agent's hooks cannot observe the external writes the registry would need. Revisit only if live evaluation shows correct application of this contract failing for lack of mutation facts that the conversation cannot hold.
