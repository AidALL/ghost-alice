# Changelog

All notable public changes to Ghost-ALICE OS should be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

Use this section for changes that have landed after the latest tagged public release.

### Added

### Changed

### Fixed

### Removed

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
