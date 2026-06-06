---
name: merge-companion
description: Use after skill updates to protect, classify, merge, discard, or defer local user changes. Handles undecided pending-merges manifest entries, install backups, a six-step UX, adversarial verification, and cross-platform decision propagation.
type: lifecycle-gate
compatibility:
  - "Python 3.11+ standard library"
---

# merge-companion

merge-companion protects local user changes during skill updates. The installer
backs up changed installed files into
`~/.ghost-alice/pending-merges/<platform>/` and records them in a manifest. At
session start, the platform hook checks the manifest and surfaces this skill
when undecided entries exist.
## Contents

- [Entry Conditions](#entry-conditions)
- [Silent Pass Conditions](#silent-pass-conditions)
- [Activation Guarantee](#activation-guarantee)
- [Six-Step Review UX](#six-step-review-ux)
- [Mandatory Adversarial Verification](#mandatory-adversarial-verification)
- [Pattern-Two Isolation And Propagation](#pattern-two-isolation-and-propagation)
- [Self-Call Restriction](#self-call-restriction)
- [io-trace](#io-trace)
- [References](#references)
- [Warnings](#warnings)


## Entry Conditions

- `~/.ghost-alice/pending-merges/<current-platform>/manifest.json` exists.
- At least one entry has `decided=false`.

## Silent Pass Conditions

Use the manifest-missing-or-invalid silent-pass rule:

- manifest missing
- `entries` empty
- JSON parse failure

All three pass without user-facing noise.

## Activation Guarantee

This skill is expected to surface if any of these layers are alive:

- pending-merge-session-start layer: SessionStart hook registered by
  `install_hooks.py`
- pending-merge-user-prompt layer: UserPromptSubmit hook payload text
- pending-merge-prose-rule layer: AGENTS.md rule 0-A
- pending-merge-task-router-precheck layer: task-router 1.0 manifest precheck
- pending-merge-bootstrap-self-check layer: platform bootstrap such as
  `~/.codex/AGENTS.md`
- pending-merge-install-tail layer: `install.sh` / `install.ps1` tail message
- pending-merge-readme layer:
  `~/.ghost-alice/pending-merges/<platform>/READ-ME-FIRST.md`
- pending-merge-session-start-failsafe layer: SessionStart hook command ending
  in `|| true`

This eight-layer list is the current operating contract.

## Six-Step Review UX

1. Notify: explain that changes were detected after the last install and show
   the backup location.
2. Classify: label each change as trivial, semantic, or structural
   using `references/impact-tier.md`. This is display sorting only;
   it does not bypass verification.
3. Show differences: provide a natural-language summary by default, plus raw
   diff on request and workflow impact.
4. Ask: offer integrate, discard, or later. `later` is the default and keeps the
   entry pending. Advanced flows may offer AI partial merge.
5. Stage merge: create the proposed result under
   `~/.ghost-alice/staged/<entry-id>/`. Do not touch the live file yet.
6. Confirm: after user approval, overwrite the live file and mark the manifest
   entry decided. If rejected, discard the staged output and leave the manifest
   entry undecided so it stays pending for a later decision.

## Mandatory Adversarial Verification

All AI output in steps 3, 4, and 5 must pass adversarial-verification before it
is shown as a decision aid or applied. Do not skip this for cost reasons or for
low-tier changes. Block immediately the moment a rationalization to skip begins.

## Pattern-Two Isolation And Propagation

- Decisions are persisted only in the current platform manifest by default.
- After step 4, ask whether the same decision should apply to other platforms.
- If yes, mark entries with the same `source_path` in other platform manifests.
- If platform-specific outputs differ, mark propagation unavailable for that
  entry.

## Self-Call Restriction

This skill may be surfaced only by:

- task-router 1.0
- AGENTS.md rule 0-A
- SessionStart hook
- explicit user request

Other skills must not call it speculatively.

## io-trace

Record manifest reads/writes, backup reads/copies, staged writes, and user
confirmation prompts in the final `[io-trace]` block.

## References

- `references/manifest-schema.md`
- `references/impact-tier.md`
- `references/merge-flow.md`

## Warnings

- Do not overwrite live installed files before user confirmation.
- Do not collapse integrate, discard, and later into a single default.
- Do not treat impact tier as verification.
- Do not propagate decisions across platforms without asking.
