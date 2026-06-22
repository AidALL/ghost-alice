# Changelog

All notable public changes to Ghost-ALICE OS should be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

Use this section for changes that have landed after the latest tagged public release.

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
