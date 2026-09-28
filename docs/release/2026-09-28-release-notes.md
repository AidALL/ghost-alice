# Ghost-ALICE OS v0.4.0 Release Notes

Language: English | [Korean](../ko/release/2026-09-28-release-notes.md)

## Changes

- Store session state, ordered events and discovery metadata in SQLite transactions. Legacy JSON/JSONL becomes validated import or explicit export material; it cannot overwrite migrated authority.
- Bind writes and completion to the exact platform, session, input receipt, revision and approved criterion definition. Share the transaction with Autopilot so task completion and Core evidence commit or roll back together.
- Preserve protected-file timestamps as exact integers in an opaque guard token, avoiding rounding-based change reports while rejecting actual changes.
- Register prospective completion capture before tool execution and provide the current prepare/publish commands. Retain the first verification result and original time instead of rerunning work solely for bookkeeping.
- Preserve the requested answer during Stop recovery and reject unsupported completion claims without turning historical failures into passes.
- Align adapter installation fixtures with both hook events and run exact-file guard regressions through the standard unittest CI discovery.

## Compatibility and upgrade

Use Core 0.4.0 with Autopilot 0.4.0. The addon now declares Core 0.4.0 as its minimum because it uses the new shared SQLite APIs. Update and reinstall both packages together. Product versions do not renumber internal schemas.

Stop legacy writers before migrating existing session state. Preserve the original files and back up a live SQLite database with its backup API or a consistent quiescent copy. Do not restore stale JSON over an initialized database. Historical release notes remain unchanged.

## Verification and limits

Existing isolated evaluations of the delivered implementation cover target/exception handling, current-source use, execution versus planning, and changed-input handling. Applicable runs published completion evidence before their first final answer without storage-only reinspection. These are selected observations, not a general reliability guarantee; original failure records and evaluation limitations remain intact. A Stop rejection can still cause another answer; this release does not remove duplicate display at the host UI level.

Release checks use the repository CI workflows. Passing deterministic checks does not prove model reasoning quality or establish untested operating-system support. Git tags and hosted release publication are separate from this source-version update.

## License

The project remains open source under Apache-2.0.
