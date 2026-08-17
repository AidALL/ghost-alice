# Changelog

All notable public changes to Ghost-ALICE OS should be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

Use this section for changes that have landed after the latest tagged public release.

## [0.2.2] - 2026-08-17

### Added

- Added a managed Claude global bootstrap with installer, status, refresh, proposal, and uninstall parity so fresh sessions outside a repository receive the same Ghost-ALICE contract as Codex.
- Added repository-local child-process runtimes plus controller-sealed purpose-hidden blind screening and one-shot Claude/Codex session transports for post-install behavioral evaluation.

### Changed

- Routing and verification now preserve the primary request and causal response order, rank the terminal objective above investigative means, and stop revalidation when no live uncertainty, decision effect, or state change remains.
- Direct-response routing now classifies before evidence planning, so a general causal premise cannot become a current-workspace diagnosis through first-person wording, ambient repository state, or verification burden.
- Claude managed skill permissions now include manifest-verified addon targets while preserving user entries, and status surfaces missing managed addon permissions without trusting arbitrary installed directories.
- Test, installer-smoke, doctor, and live-session scratch state now stays under the owning repository's `.tmp` tree; successful runs clean up and failed runs preserve a reported diagnostic path.
- Installer progress output now preserves semantic fields without character-count padding or an 80-column special formatter.
- Long `shared-all` and `scripts-all` release and CI runs now report verbose method-level progress instead of remaining silent until the full serial suite exits.
- Source prose no longer uses display-width hard wrapping, and the release gate rejects newly introduced width-only Markdown wrapping while preserving structural, syntax, and protocol boundaries.
- Adversarial verification now stops after a complete independent cycle when decision-relevant uncertainty and state cease changing, while preserving independent attack roles, unanimity, fail-closed disagreement, and the finite safety cap.

### Fixed

- Preserved both empty-object and explicit continue/no-message hook protocol JSON across reduced visibility profiles.
- Prevented current-result explanations from recursively reopening verification merely because a new user turn arrived.
- Detected Windows directory junction installs as `junction` instead of `copy`, required live and recorded install modes to agree, rejected base and descendant junction write-through, and repaired dangling junctions through the shared link owners.
- Treated `SystemExit(None)`, `SystemExit(0)`, and `SystemExit(False)` as successful project-runtime exits so successful command wrappers clean their repository-local scratch directories while failure exits preserve diagnostics.
- Accepted native Windows absolute paths and filesystem roots in the agent security surface scanner instead of rejecting them through POSIX-only normalization.
- Preserved configured Claude and Codex homes in fresh blind sessions while keeping evaluator-only rubric and purpose data out of the subject process.
- Capped fresh Claude and Codex Git ancestor discovery at the run root so repository-local scratch remains visible and recoverable without turning the parent release worktree into subject evidence.
- Cleared stale PowerShell progress-frame suffixes without restoring fixed-width padding, and moved Claude full-uninstall global-rule removal into the shared machine-readable cleanup report.
- Restored filesystem discovery for source scans rooted below an ignored parent worktree when the requested root has no Git metadata.

### Removed

- Removed duplicate Codex hookless-fallback renderers and their dead shell/PowerShell bootstrap helpers; the platform bootstrap remains the canonical installed source.

## [0.2.1] - 2026-07-01

### Added

- Live Codex smoke harness coverage for completion-check and install-doctor prompts, including hook-trust flag compatibility and Windows command shim handling.
- Runtime doctor audits for installed shared hook dependencies so partial installs and missing runtime files surface as first-class diagnostics.
- Acceptance-criteria lifecycle tracking for admitted, met, and unmet completion criteria.
- Structured Bash io-trace fields (`op` and `path`) so downstream runtime signals can render platform-neutral file activity while preserving the raw command in the local audit log.

### Changed

- Codex installer and doctor flows now sync and validate the live runtime shared core more directly after installation.
- Session-intent, completion-check, uninstall, and io-trace paths now preserve clearer failure boundaries for Codex and Windows runs.
- Live smoke classification now treats recovered runtime router errors as pass only when the run exited cleanly, produced output, and emitted every required governance marker.

### Fixed

- Prevented Korean post-verbal negated status text from being misread as a completion claim while keeping real completion claims detectable.
- Added the pending-merge message helper to runtime doctor dependency audits so transitive hook imports cannot drift silently.
- Distinguished absent session-intent ledger modules from present-but-broken imports without blocking the hook.
- Removed Windows reparse-point addon targets safely during uninstall without recursing into junction or MSYS symlink targets.
- Skipped Bash-backed addon lifecycle tests cleanly when a discovered Bash path cannot actually be launched.
- Preserved `met_at` when already-met acceptance criteria are merged again.
- Scoped Windows io-trace paths and structured shell rows correctly for cross-platform continuation consumers.
- Added line-ending and compatibility guards for shell, PowerShell, and Codex command resolution surfaces.

## [0.2.0] - 2026-06-22

### Added

- In-repository official addon reference pages and homepage links so addon usage no longer depends only on wiki availability.
- Public docs rendering support for the homepage, docs index, release pages, and official addon references.
- Installer and test coverage for official autopilot addon aliases, remote addon source installs, privileged adapter provisioning, and uninstall cleanup.

### Changed

- Official autopilot addon installation docs now clarify that the command runs from the Ghost-ALICE core checkout and that the addon repository compatibility matrix is the authority for full runtime compatibility claims.
- Plan execution, TDD, task routing, and coding-convention guidance now route scope substitution, unnecessary broad reruns, and stale plan/task tracker state through explicit governance checks.
- Installer progress, report, and PowerShell/CMD surfaces now preserve addon target counts and platform-specific install semantics more consistently.

### Fixed

- Hardened Bash and PowerShell autopilot addon install/uninstall behavior, including hook marker ownership, adapter cleanup, sidecar handling, and selected uninstall guards.
- Fixed public documentation link behavior where rendered pages previously fell through to raw Markdown or stale wiki-only targets.
- Kept public installer and docs validation aligned after the addon install UX, official addon docs, and release-prep changes.

### Removed

## [0.1.3] - 2026-06-18

### Added

- Core addon registry: each installed addon gets a per-addon sidecar under `~/.ghost-alice/addons/<platform>/` that records every target it provides plus a content hash, so install and uninstall can prove ownership before touching anything on disk.
- Hash-gated, crash-resumable per-addon uninstall: a drifted or user-modified target is preserved for manual review, and only managed hooks and files the registry can prove it owns are removed.
- Install-time collision preflight now also covers addon command and resource extras (not just skills), so a destination already owned by something else aborts the install before any hook or skill is written.
- Enforced `depends_on_core` at install time: a declared core dependency must exist in the selected core skill set.
- PowerShell installer parity for the addon uninstall safety gates (full-uninstall hash gate, and selected-uninstall dependency guard with `-Force`).
- Skill validator gate for SKILL.md table-of-contents to heading parity.

### Changed

- Addon hook ownership is proven by an exact marker plus hook-runner token and argv match for both install-time stale pruning and uninstall, so a user hook that merely contains a marker substring is never removed.

## [0.1.2] - 2026-06-14

### Changed

- Routed installer update guidance through safe source updaters so tracked command additions do not push users toward raw `git pull` collisions with local untracked files.
- Hardened installer pending-merge cleanup and source auto-detection coverage for public update workflows.

### Fixed

- Guarded the public core catalog and public-surface validator against externalized addon skills or command wrappers re-entering the core repository.
- Kept the installer encoding guard from scanning local `.tmp` experiment artifacts that are excluded from public release snapshots.

### Removed

- Removed bundled addon residue, including the design-library reference bundle and public installer/docs references to externalized addon names.

## [0.1.1] - 2026-06-08

### Fixed

- Prevented Claude hook status and checkpoint surfaces from leaking into final answer text.
- Reduced duplicate tool-checkpoint user-facing output while keeping every tool call checked.
- Kept Claude Stop-hook retries as complete standalone answers instead of replacing the answer body with process notes.
