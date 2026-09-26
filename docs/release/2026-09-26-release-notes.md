# Ghost-ALICE OS v0.3.0 Release Notes

Language: English | [Korean](https://github.com/AidALL/ghost-alice/blob/v0.3.0/docs/ko/release/2026-09-26-release-notes.md)

Date: 2026-09-26

This release preserves corrected user intent within each session and across downstream consumers, improves hook delivery and installation behavior, and aligns the public release number with Ghost-ALICE Autopilot `v0.3.0`.

This file is the release-body source of truth for `v0.3.0`. The GitHub Release body and `CHANGELOG.md` must describe the same release. Historical release notes remain unchanged.

## Main Changes

- Ledger snapshots preserve non-superseded decision bodies in `active_decisions` and the recorded `latest_scope`, so routing and skill-evolution receive the context needed to interpret a correction. These fields do not independently grant permission or retire a restriction.
- Correction capture distinguishes an asserted or observed prior mismatch from a new instruction, preventive restriction, or request to refresh a previously valid result. Re-reading a lesson does not count as another correction occurrence.
- Scope handling reconciles current instructions with accumulated constraints. A clear current instruction can authorize its bounded change without formal revocation wording; conflicting compressed fields alone cannot establish which boundary came first.
- Semantic CLI writes follow the native session identity and the completed intake receipt. A different session cannot become the write target merely because a shared `current-session.json` pointer changed. Explicit read-only historical snapshots remain available.
- Intake receipts reach the model through hook context. Reducing visible governance messages preserves the JSON fields that control continuation, blocking, and model context.
- The installer registers exact verified addon event/command pairs and retains a validated installation-time Python interpreter as the final fallback after runtime overrides and normal discovery. It does not change the host's global Python or `PATH`.
- Codex hook observations and downstream consumers honor the native thread identity before shared-pointer fallback when the payload has no session ID. Manual security-decision writes use the selected root, platform, and session explicitly.
- Confirmed current tool blocks remain denied when the derived gate cannot be persisted; absent, stale, or allow decisions do not acquire a new block from that error handling.
- Before publishing, the coding workflow inventories the actual CI workflows and runs every relevant local equivalent, recording runner differences. Integration separately requires successful CI and completed GitHub review for the current commit, including inline findings. A rewritten commit requires fresh remote results.
- English and Korean public guidance describe the same behavior, with polite Korean README prose and links to the coordinated release documentation.

## Coordinated Autopilot Release

The recommended pair is Ghost-ALICE OS `0.3.0` and Ghost-ALICE Autopilot `0.3.0`. The addon remains a separate package installed through core. Its declared technical minimum remains core `0.2.2`; matching release numbers do not change that dependency floor. Use the coordinated pair to receive the current core intent fixes and addon continuation fixes together.

The [Autopilot update](https://github.com/AidALL/ghost-alice-autopilot/pull/18) checks the selected current intent before applying pending receipts or plans. Its Stop adapter also resolves explicitly selected `agent-runtime` ledgers using an explicit session ID and absolute intent root. Missing or conflicting context cannot fall through to another platform or session. Valid receipts are consumed once, and refinements within an approved objective retain that approval.

This adapter contract does not install a model backend, tool executor, or host event loop. Those remain the host's responsibility. The standalone bridge CLI still targets Claude and Codex, and platform claims remain bounded by the addon's `compatibility-matrix.json`.

## Verification Evidence And Limits

Public regression sources cover the following contracts:

- [Ledger regression tests](https://github.com/AidALL/ghost-alice/blob/v0.3.0/session-intent-analyzer/scripts/test_session_intent_ledger.py) exercise preserved decisions/scope and session-bound semantic writes.
- [Hook regression tests](https://github.com/AidALL/ghost-alice/blob/v0.3.0/_shared/test_session_intent_analyzer_hook.py) exercise intake observations, receipts, and session selection.
- [Autopilot adapter regression tests](https://github.com/AidALL/ghost-alice-autopilot/blob/v0.3.0/tests/test_privileged_adapter.py) exercise current-intent checks, approval boundaries, and receipt consumption.

Recorded-state replay and model interpretation trials were also run against owner-provided material. Their private metrics are omitted here because the underlying artifacts are not published with this release. Public test sources show the regression coverage; they are not a substitute for those private run results.

Both Claude and Codex installation paths were checked. The current intent-interpretation and action trials used Codex; this release does not claim fresh Claude model inference from those trials. Structural checks do not establish semantic accuracy, reconstructed cases do not replay every historical conversation, and these regressions do not establish two-week endurance or universal model/platform compatibility. Original failures and evaluation exclusions remain in the owner-delivered evidence.

For the implementation and validation summaries, see [core PR 44](https://github.com/AidALL/ghost-alice/pull/44) and [Autopilot PR 18](https://github.com/AidALL/ghost-alice-autopilot/pull/18). Private ledgers, raw conversations, credentials, and host configuration backups are not part of the public release.

## Updating And Checking The Installation

Follow the [installation and update guide](https://github.com/AidALL/ghost-alice/blob/v0.3.0/docs/getting-started/installation.md). Update the core source, reinstall the intended platform and addon, and use `--status` and `--doctor` to inspect the installed result. If source update reports a conflict, follow the recovery guide before trying again; do not overwrite local changes to force an update.

The product `VERSION`, Git tag, changelog section, and release body must agree on `0.3.0`. The addon's repository version, `addon_version`, release tag, and installed sidecar must likewise agree on `0.3.0`. Internal hook versions, catalog versions, and data schema versions are independent and are not renumbered by this release.

## License

The project-owned source code and documentation remain under Apache License, Version 2.0. [LICENSE](https://github.com/AidALL/ghost-alice/blob/v0.3.0/LICENSE), [NOTICE](https://github.com/AidALL/ghost-alice/blob/v0.3.0/NOTICE), and [THIRD_PARTY_NOTICES.md](https://github.com/AidALL/ghost-alice/blob/v0.3.0/THIRD_PARTY_NOTICES.md) retain their existing boundaries. Aligning release versions does not change the public license.
