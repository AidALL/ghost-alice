# Public Release Checklist

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/release/public-release-checklist.md)

This checklist prepares a clean Ghost-ALICE OS public repository snapshot without private repository history.
## Contents

- [Release Goal](#release-goal)
- [Include](#include)
- [Exclude](#exclude)
- [Pre-Export Checks](#pre-export-checks)
- [License And Provenance Gates](#license-and-provenance-gates)
- [Sensitive String Scan](#sensitive-string-scan)
- [GitHub Community File Check](#github-community-file-check)
- [Clean Export Procedure](#clean-export-procedure)
- [GitHub Repository Setup](#github-repository-setup)
- [Public Smoke Test](#public-smoke-test)


## Release Goal

Create a public repository snapshot that includes the core Ghost-ALICE OS governance layer, public installer, public skills, public docs, and validation scripts, while excluding local state, secrets, private addons, tenant content, and private development artifacts.

## Include

- `README.md`
- `LICENSE`
- `NOTICE`
- `THIRD_PARTY_NOTICES.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `SUPPORT.md`
- `CHANGELOG.md`
- `AGENTS.md`, `CLAUDE.md`
- `install.sh`, `install.ps1`, `install.cmd`
- `installer_lib/`
- `_shared/`
- public skill directories
- `skill-catalog/`
- `scripts/`
- `platforms/`
- `.claude/commands/`
- `docs/`
- `official-docs/`
- `design-library/` (bundled MIT reference data; include with its upstream notice or exclude. See License And Provenance Gates and `THIRD_PARTY_NOTICES.md`)
- public images under `imgs/`
- `.github/`

## Exclude

- `.git/` history from the private development repo
- `.tmp/`
- `.worktrees/`
- local install logs and reports
- `~/.ghost-alice/` runtime state
- `~/.agents/`, `~/.claude/`, `~/.codex/` installed copies
- `.claude/settings.local.json`
- `.claude/settings.json`
- `~/.ghost-alice/secrets.env`
- private addons and tenant skills
- customer, grant, company, or credential files
- generated caches such as `__pycache__/`
- local editor state

## Pre-Export Checks

Run from the private working repo before creating the clean export.

```bash
git status --short
python3 scripts/validate_public_surfaces.py
python3 scripts/check_skill_gate_contract.py
python3 -m unittest discover -s _shared -p 'test_*.py'
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

For installer-heavy releases, also run:

```bash
python3 scripts/run_installer_compat_tests.py
python3 scripts/validate_platform_adapters.py
```

## License And Provenance Gates

- Confirm `LICENSE` is the unmodified Apache License, Version 2.0 text.
- Confirm `README.md`, `NOTICE`, and `THIRD_PARTY_NOTICES.md` agree on the project license boundary.
- Confirm bundled third-party reference material has source URL, license, copyright notice, and local path.
- Confirm `design-library/` remains covered by its upstream MIT notice or is excluded.
- Do not publish copied upstream documentation snapshots. Keep only original project commentary with short citations and source locators.
- Confirm no contributor-facing file implies a CLA, DCO, or inbound license rule that maintainers do not actually enforce.

## Sensitive String Scan

Before export, scan for obvious private material.

```bash
rg -n --hidden --glob '!/.git/**' --glob '!/.tmp/**' \
  'api[_-]?key|token|password|secret|private[_-]?key|BEGIN [A-Z ]*PRIVATE KEY|secrets.env|company-info-files|customer|grant'
```

Review hits manually. Some hits are expected in policy text and secret helper documentation.

## GitHub Community File Check

- Confirm `README.md`, `LICENSE`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, issue templates, and pull request template are present.
- Confirm `.github/CODEOWNERS` is absent until real maintainers or teams exist, or contains only real GitHub users or teams with write access.
- Confirm there is no duplicate issue template pair for the same workflow.
- Confirm security reports route to `SECURITY.md` or GitHub private vulnerability reporting, not public issues.

## Clean Export Procedure

1. Create a fresh directory outside the private repo.
2. Copy only included paths.
3. Run the pre-export checks inside the export.
4. Initialize a new Git repository.
5. Commit the clean snapshot as the initial public commit.
6. Push to the public repository.
7. Confirm GitHub community profile status.
8. Confirm CI passes on the public repository.
9. Confirm branch protection and CODEOWNERS review settings only after real maintainers or teams exist.

## GitHub Repository Setup

Enable or configure:

- Private vulnerability reporting, if available
- Branch protection for the default branch
- Required CI checks
- Dependabot or equivalent dependency update automation, when package manifests are introduced
- OpenSSF Scorecard workflow, once the public repository is stable

## Public Smoke Test

After publication, test a clean clone.

```bash
git clone <public-repo-url> ghost-alice-public-smoke
cd ghost-alice-public-smoke
python3 scripts/validate_public_surfaces.py
bash install.sh --status
```

Do not run install commands that mutate your main development environment unless the smoke test is intentionally using a disposable account or machine.
