# Ghost-ALICE OS v0.2.1 Release Notes

Date: 2026-07-01

Scope: current `main` after `v0.2.0`, including governance runtime hardening, acceptance-criteria lifecycle changes, and the cross-platform io-trace/runtime diagnostic fixes prepared for `v0.2.1`.

Status: release documentation prepared for tag `v0.2.1`; GitHub Release publication follows the branch, PR, CI, and merge flow.

## Main Changes

- Added live Codex smoke coverage for completion-check and install-doctor prompts, including command-resolution and hook-trust compatibility handling.
- Hardened installer doctor diagnostics so installed runtime shared files, hook runner dependencies, partial installs, and missing session-intent dependencies are classified precisely.
- Added acceptance-criteria lifecycle state and preserved already-met criteria when ledger updates are merged again.
- Tightened completion-check validation around Korean post-verbal negation, negated status text, and later executed-work success claims.
- Added structured Bash io-trace `op`/`path` extraction while preserving the raw command in the local audit record.
- Improved Windows uninstall handling for reparse points, io-trace path normalization, and line-ending coverage for cross-platform installer and hook surfaces.
- Kept live smoke classification from false-failing fully governed runs that recovered from transient router diagnostics, while still failing incomplete runs.

## Verification Surface

- Local unit verification should cover `_shared` tests, `scripts/tests`, public surface validators, installer compatibility checks, and platform adapter validation.
- Korean public-doc counterpart validation should include `docs/ko/release/2026-07-01-release-notes.md`.
- Live Codex smoke verification should cover `completion-check-readme` and `install-doctor-read` cases against the installed runtime.
- GitHub Actions must pass on the release PR before publishing `v0.2.1`.

## Compatibility Boundary

- This release improves Codex and Windows runtime diagnostics, but it does not expand the public compatibility claim beyond the repository compatibility policies.
- The official autopilot addon remains versioned in the separate `ghost-alice-autopilot` repository and is released independently.

## Release Boundary

- This note does not create the tag or GitHub Release by itself.
- Use this note and `CHANGELOG.md` as the release body source for `v0.2.1`.
