---
name: merge-companion
description: Reviews the user's backed-up edits to installed skills and merges each into the new skill version, discards or defers it as the user chooses, before a task changes or reinstalls an installed skill, or when the user asks for that review.
type: lifecycle-gate
compatibility:
  - "Python 3.11+ standard library"
  - "Ghost-ALICE _shared folder, installed beside the skill"
---

# Merge Companion

The response model uses this workflow to protect user changes that the installer backed up. The installer records changed installed files under `~/.ghost-alice/pending-merges/<platform>/`; the response model reviews the undecided manifest entries before applying the result the user chose.

## Entry and activation

The response model applies this skill in the two cases below. In both, it first reads the current host's manifest again, because an installer run can add entries during the session.

- Before selected work modifies, replaces or installs an installed skill, the response model runs the review below only for an undecided entry of that skill. Without such an entry, including when the manifest is missing, has no entries or fails to parse as JSON, the work continues without a notice.
- When the user asks to review backed-up changes, the response model runs the review below for every undecided entry. When there is none, it tells the user that no backed-up change is waiting, or that the manifest could not be read.

A read-only lookup unrelated to those changes does not load this skill, read backups or ask for a merge decision. Other skills invoke this skill only in the two cases above.

The session-start hook, the installer's closing output, the pending-merges READ-ME-FIRST file and the session-start failsafe command ending in `|| true` report the same pending entries. Their reports do not authorize a merge or extend this workflow to unrelated work. When the user explicitly defers the review, other authorized work continues and the backups stay undecided.

## Review and apply

1. The response model explains that the installer detected local changes and gives the backup location.
2. It classifies each change as trivial, semantic or structural using `references/impact-tier.md`. The tier controls display order and detail only.
3. It describes each diff and its effect on the workflow in natural language, and shows the raw diff on request. Hash and provenance evidence establishes identity, drift and merge safety; evidence from the current contract and behavior decides which meaning to keep.
4. It offers integrate, discard or later. Later is the default and leaves the entry pending. An advanced flow may offer a partial merge.
5. For integration, it prepares the proposed result under `~/.ghost-alice/staged/<entry-id>/` without changing the live file.
6. After the user approves the staged result, it follows [Apply and record a decision](references/manifest-schema.md#apply-and-record-a-decision): it applies the change to the live file through the host's file tool, then records the manifest entry through the importable helper. If the user rejects the proposal, it removes the staged result and leaves the entry undecided.

The content that the response model prepares for the user's decision in steps 3–5 passes adversarial-verification before the response model presents or applies it. Low impact or low cost does not waive that requirement. The response model must not use a tier as verification evidence.

## Propagation and decision state

By default, a decision changes only the current host's manifest. After step 4, the response model asks whether the decision also applies to the other hosts, unless the user's instructions already give that choice. If the user authorizes it, the response model reads the matching `source_path` entries and stages each result under that host's contract. Each approved result then follows [Apply and record a decision](references/manifest-schema.md#apply-and-record-a-decision) with that host in place of the current host. When the result would differ between hosts, the decision does not propagate for that entry.

The response model marks an entry merged or discarded only after the approved change or discard completes. Approval or staging alone does not prove that the change was applied. The option `later` records `deferred` and keeps `decided=false`. The lifecycle and safety fields are in `references/manifest-schema.md`; conflicts and non-interactive runs are in `references/merge-flow.md`.

## Audit and warnings

The response model keeps evidence of manifest operations, backup reads and copies, staged writes and user confirmations without displaying it.
