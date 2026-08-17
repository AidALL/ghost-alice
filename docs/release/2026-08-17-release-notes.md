# Ghost-ALICE OS v0.2.2 Release Notes

Date: 2026-08-17

Scope: `v0.2.2` upgrades the governance behavior, Claude/Codex installation parity, repository-local execution isolation, purpose-hidden installed-agent evaluation, Windows junction handling, and release verification surfaces prepared after `v0.2.1`.

Status: this file is the release-body source of truth for `v0.2.2`; the published release body must remain synchronized with this file and `CHANGELOG.md`.

## Main Changes

- Preserved the primary request and causal response order, ranked terminal objectives above requested means, and stopped verification when no decision-relevant uncertainty or state delta remains.
- Classified direct responses before evidence planning and isolated fresh-session Git discovery, preventing ambient repository state from becoming an unrequested local diagnosis.
- Added managed Claude global bootstrap installation, status, refresh, permission, and uninstall parity with the Codex global bootstrap.
- Added repository-local child runtimes and canonical test launchers so child temp, Python cache, clean working directories, and failed-run diagnostics stay under the owning repository `.tmp` tree.
- Added controller-sealed blind behavior evaluation that loads the held-out case itself, binds evidence to exact case content, invokes only fresh ephemeral Claude/Codex sessions, and keeps evaluator-only rubric and purpose data away from the subject.
- Hardened Windows addon junction handling across install-mode detection, collision proofs, safe provisioning, content hashing, dangling-target repair, uninstall fixtures, and cross-shell tests.
- Removed width-based prose wrapping and installer progress padding, added a structural Markdown wrapping gate, and preserved semantic output without truncation.
- Removed duplicate verification and hookless-fallback owners, bounded adversarial convergence by decision value, and centralized Claude full-uninstall rule removal in the shared report owner.

## Verification Surface

- Run `python scripts/run_installer_compat_tests.py --group shared-all` and `python scripts/run_installer_compat_tests.py --group scripts-all` with method-level progress enabled.
- Run public-surface, skill-contract, skill, platform-adapter, entrypoint, prose-wrap, and merge-companion validators.
- Install core and the compatible autopilot addon for Claude and Codex, then run status and doctor checks before behavioral evaluation.
- Run evaluator-visible smoke separately from the installed Claude and Codex blind-controller cases; only the latter is hidden-purpose behavioral release evidence.
- Require all five purpose-hidden public-prompt cases to pass every evaluator dimension in fresh installed Claude and Codex sessions.

## Compatibility Boundary

- The official `ghost-alice-autopilot` addon release paired with this core is `v0.1.2`, whose minimum core version is `0.2.2`.
- Platform claims remain bounded by the repository compatibility matrix and actual installed-session evidence.

## Release Boundary

- Before publication is considered complete, repository `VERSION`, the Git tag, the changelog section, and the release body must identify `v0.2.2`; installed status must report healthy installer-owned source linkage and the managed global bootstrap for each installed platform.
