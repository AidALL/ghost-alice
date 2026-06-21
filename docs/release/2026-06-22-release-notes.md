# Ghost-ALICE OS Release Prep Notes

Date: 2026-06-22

Scope: changes after `v0.1.3` through `c8e2430876a74263f6fa2af6b3e554d53bcf377e`.

Status: release-prep complete; tag and GitHub Release publication are separate follow-up actions.

## Main Changes

- Hardened official addon installation and removal paths across Bash, PowerShell, Codex, and Claude-oriented install surfaces.
- Added and validated official autopilot addon alias handling, addon source preparation, adapter provisioning, sidecar ownership, and uninstall cleanup.
- Clarified that autopilot is installed from the Ghost-ALICE core checkout and that the addon repository `compatibility-matrix.json` owns full runtime compatibility claims.
- Updated task-router, coding-convention, TDD, and executing-plans guidance so scope substitution, stale plan/task tracker state, and unnecessary broad reruns are handled as governance failures.
- Refreshed public documentation, rendered homepage/docs surfaces, and official addon references to avoid stale wiki-only and raw Markdown targets.

## Verification Evidence

- Local full test suite: `PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3 -m pytest -q` -> `970 passed, 15 skipped, 289 subtests passed`.
- Local release-surface checks:
  - `/opt/homebrew/bin/python3 scripts/check_skill_gate_contract.py`
  - `/opt/homebrew/bin/python3 scripts/validate_skills.py --json`
  - `/opt/homebrew/bin/python3 scripts/validate_public_surfaces.py`
- GitHub Actions on `main` at `c8e2430876a74263f6fa2af6b3e554d53bcf377e`: `CI`, `Skill Validation`, and `skill-gate-contract` succeeded.
- PR #21 was merged into `main`; the merged commit range was authored and committed by `garlicvread <ceo@aidall.tech>`.

## Release Boundary

- This note does not create a tag or GitHub Release.
- Before publication, choose the release version and use this note plus `CHANGELOG.md` as the release body source.
- Autopilot full runtime compatibility remains governed by the separate addon repository compatibility matrix.
