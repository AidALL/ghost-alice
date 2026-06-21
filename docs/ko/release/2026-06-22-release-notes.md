# Ghost-ALICE OS Release Prep Notes

언어: [🇺🇸 English](../../release/2026-06-22-release-notes.md) | 🇰🇷 한국어

Date: 2026-06-22

Scope: `v0.1.3` 이후부터 `c8e2430876a74263f6fa2af6b3e554d53bcf377e`까지의 변경.

Status: release-prep complete. tag와 GitHub Release publication은 별도 후속 작업이다.

## Main Changes

- Bash, PowerShell, Codex, Claude-oriented install surface 전반에서 official addon 설치와 제거 경로를 강화했다.
- official autopilot addon alias handling, addon source preparation, adapter provisioning, sidecar ownership, uninstall cleanup을 추가하고 검증했다.
- autopilot은 Ghost-ALICE core checkout에서 설치하며, full runtime compatibility claim은 addon repository의 `compatibility-matrix.json`이 소유한다고 명확히 했다.
- task-router, coding-convention, TDD, executing-plans guidance를 업데이트해 scope substitution, stale plan/task tracker state, unnecessary broad rerun을 governance failure로 다루도록 했다.
- stale wiki-only target과 raw Markdown target을 피하도록 public documentation, rendered homepage/docs surface, official addon references를 정리했다.

## Verification Evidence

- Local full test suite: `PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3 -m pytest -q` -> `970 passed, 15 skipped, 289 subtests passed`.
- Local release-surface checks:
  - `/opt/homebrew/bin/python3 scripts/check_skill_gate_contract.py`
  - `/opt/homebrew/bin/python3 scripts/validate_skills.py --json`
  - `/opt/homebrew/bin/python3 scripts/validate_public_surfaces.py`
- GitHub Actions on `main` at `c8e2430876a74263f6fa2af6b3e554d53bcf377e`: `CI`, `Skill Validation`, and `skill-gate-contract` succeeded.
- PR #21 was merged into `main`; merged commit range는 `garlicvread <ceo@aidall.tech>`가 author와 committer로 기록했다.

## Release Boundary

- 이 note는 tag 또는 GitHub Release를 생성하지 않는다.
- publication 전에 release version을 정하고 이 note와 `CHANGELOG.md`를 release body source로 사용한다.
- Autopilot full runtime compatibility는 별도 addon repository compatibility matrix가 계속 관리한다.
