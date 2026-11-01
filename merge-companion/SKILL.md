---
name: merge-companion
description: Reviews the user's backed-up edits to installed skills and merges each into the new skill version, discards or defers it as the user chooses, before a task changes or reinstalls an installed skill, or when the user asks for that review.
type: lifecycle-gate
compatibility:
  - "Python 3.11+ standard library"
---

# Merge Companion

The response model uses this lifecycle workflow to protect user changes backed up by a skill installer. The installer records changed installed files under `~/.ghost-alice/pending-merges/<platform>/`; the response model reviews undecided manifest entries before applying a user's chosen result.

## Entry and activation

The response model applies this skill in two cases. In both, it must first re-check the current platform's manifest; an earlier observation does not answer that check because an installer run can add entries during the session.

- Before selected work modifies, replaces or installs an installed skill, the review below runs only for an undecided entry for that skill. Without one, including a missing manifest, empty entries or a JSON parse failure, the work continues without a notice.
- When the user asks to review backed-up changes, the review below runs for every undecided entry. Without one, the response model tells the user that no backed-up change is waiting, or that the manifest could not be read.

A bounded read-only lookup unrelated to those changes does not load this procedure, inspect backups or request a merge decision. Other skills must not invoke it speculatively.

The session-start hook, installer tail, pending-merges READ-ME-FIRST and session-start failsafe command ending in `|| true` surface the same pending entries. Their reports do not authorize a merge or expand this workflow to unrelated work. An explicit user defer permits other authorized work and leaves the backups undecided.

## Review and apply

1. The response model explains that the installer detected local changes and gives the backup location.
2. It classifies each change as trivial, semantic or structural using `references/impact-tier.md`. The tier controls display order and detail only.
3. It presents a natural-language diff and workflow impact, with raw diff on request. Hash/provenance evidence establishes identity, drift and merge safety; current contract and behavior evidence determine which semantics to retain.
4. It offers integrate, discard or later. Later is the default and leaves the entry pending. An advanced flow may offer a partial merge.
5. For integration, it prepares the proposed result under `~/.ghost-alice/staged/<entry-id>/` without changing the live file.
6. After approval of the staged result, it follows [Apply and record a decision](references/manifest-schema.md#apply-and-record-a-decision): apply the live change through the host file tool, then record the exact manifest entry through the importable helper. If the user rejects the proposal, it removes the staged result and leaves the entry undecided.

All AI decision-aid content in steps 3–5 passes adversarial-verification before presentation or application. Low impact and cost do not waive that requirement. The response model must not use a tier as verification evidence.

## Propagation and decision state

The default decision affects only the current platform manifest. After step 4, the response model asks whether the decision also applies to other platforms unless the user's existing instruction already supplies that choice. If authorized, it inspects matching source_path entries and stages each result under that platform's contract. Platform-specific differing outputs make propagation unavailable for that entry.

The response model marks an entry merged or discarded only after the corresponding approved application or discard completes. Approval or staging alone does not prove application. Later records deferred while keeping decided=false. The lifecycle and safety fields are in `references/manifest-schema.md`; the worked flow is in `references/merge-flow.md`.

## Audit and warnings

The response model retains manifest operations, backup reads/copies, staged writes and confirmation evidence internally.

The response model must not overwrite live files before required approval, merge the three choices into one default, bypass adversarial verification or propagate without authority.
