# Changelog

All notable public changes to Ghost-ALICE OS should be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

Use this section for changes that have landed after the latest tagged public release.

### Added

### Changed

### Fixed

### Removed

## [0.1.1] - 2026-06-08

### Fixed

- Prevented Claude hook status and checkpoint surfaces from leaking into final answer text.
- Reduced duplicate tool-checkpoint user-facing output while keeping every tool call checked.
- Kept Claude Stop-hook retries as complete standalone answers instead of replacing the answer body with process notes.
