# Public Release Checklist

언어: [🇺🇸 English](../../release/public-release-checklist.md) | 🇰🇷 한국어

이 checklist는 private repository history 없이 깨끗한 Ghost-ALICE OS public repository snapshot을 준비한다.
## Contents

- [Release Goal](#release-goal)
- [Include](#include)
- [Exclude](#exclude)
- [Pre-Export Checks](#pre-export-checks)
- [Installed Behavior Gates](#installed-behavior-gates)
- [License And Provenance Gates](#license-and-provenance-gates)
- [Sensitive String Scan](#sensitive-string-scan)
- [GitHub Community File Check](#github-community-file-check)
- [Clean Export Procedure](#clean-export-procedure)
- [GitHub Repository Setup](#github-repository-setup)
- [Public Smoke Test](#public-smoke-test)


## Release Goal

local state, secrets, private addons, tenant content, private development artifacts는 제외하고 core Ghost-ALICE OS governance layer, public installer, public skills, public docs, validation scripts를 포함한 public repository snapshot을 만든다.

## Include

- `README.md`
- `README_ko.md`
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
- public images under `imgs/`
- `.github/`

## Exclude

- private development repo의 `.git/` history
- `.tmp/`
- `.worktrees/`
- local install logs and reports
- `~/.ghost-alice/` runtime state
- `~/.agents/`, `~/.claude/`, `~/.codex/` installed copies
- `.claude/settings.local.json`
- `.claude/settings.json`
- `~/.ghost-alice/secrets.env`
- private addons and tenant skills
- customer, grant, company, credential files
- generated caches such as `__pycache__/`
- local editor state

## Pre-Export Checks

clean export를 만들기 전에 private working repo에서 실행한다.

```bash
git status --short
python3 scripts/validate_public_surfaces.py
python3 scripts/check_skill_gate_contract.py
python3 scripts/check_prose_wrapping.py
python3 scripts/run_installer_compat_tests.py --group shared-all
python3 scripts/run_installer_compat_tests.py --group scripts-all
```

installer-heavy release에서는 다음도 실행한다.

```bash
python3 scripts/run_installer_compat_tests.py
python3 scripts/validate_platform_adapters.py
```

## Installed Behavior Gates

hook, gate, control-surface marker를 검증하려면 `docs/ko/policies/live-smoke-regression.md`의 evaluator-visible governance live smoke를 실행한다. 이 smoke는 runtime-plumbing evidence이며 blind behavior gate를 충족하지 않는다.

candidate가 canonical하게 install되고 installer-owned manifest가 있는 disposable environment에서 installed subject별로 separate blind fresh-session controller를 한 번씩 실행한다. sealed case와 trusted evaluator placeholder를 release-owned path 또는 command로 바꾸며 evaluator-private material을 subject command나 environment에 넣지 않는다.

```bash
python3 -m _shared.blind_behavior --case <sealed-held-out-case.json> --platform claude --subject-command '["claude"]' --evaluator-command '["<trusted-evaluator>"]' --record .tmp/release/blind-claude.json
python3 -m _shared.blind_behavior --case <sealed-held-out-case.json> --platform codex --subject-command '["codex"]' --evaluator-command '["<trusted-evaluator>"]' --record .tmp/release/blind-codex.json
```

두 command 모두 exit zero여야 하며 sanitized record에 `verdict=pass`, expected installed platform provenance, controller-computed suite digest가 있어야 한다. prompt, rubric, purpose, expected answer, subject response, evaluator-private text는 record에 없어야 한다. 한 platform의 pass는 다른 platform을 cover하지 않으며 automated blind result도 human-operated clean-terminal acceptance gate를 대체하지 않는다.

## License And Provenance Gates

- `LICENSE`가 수정되지 않은 Apache License, Version 2.0 text인지 확인한다.
- `README.md`, `README_ko.md`, `NOTICE`, `THIRD_PARTY_NOTICES.md`가 project license boundary에 대해 일치하는지 확인한다.
- bundled third-party reference material이 포함되는 경우 source URL, license, copyright notice, local path가 있는지 확인한다.
- copied upstream documentation snapshots를 publish하지 않는다. short citations와 source locators가 있는 original project commentary만 둔다.
- contributor-facing file이 maintainers가 실제로 enforce하지 않는 CLA, DCO, inbound license rule을 암시하지 않는지 확인한다.

## Sensitive String Scan

export 전에 obvious private material을 scan한다.

```bash
rg -n --hidden --glob '!/.git/**' --glob '!/.tmp/**' \
  'api[_-]?key|token|password|secret|private[_-]?key|BEGIN [A-Z ]*PRIVATE KEY|secrets.env|company-info-files|customer|grant'
```

hit를 수동 review한다. policy text와 secret helper documentation에는 일부 expected hit가 있을 수 있다.

## GitHub Community File Check

- `README.md`, `README_ko.md`, `LICENSE`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, issue templates, pull request template이 있는지 확인한다.
- `.github/CODEOWNERS`는 real maintainer 또는 team이 생기기 전까지 없거나, write access가 있는 real GitHub users/teams만 포함해야 한다.
- 같은 workflow에 대한 duplicate issue template pair가 없는지 확인한다.
- security reports는 public issues가 아니라 `SECURITY.md` 또는 GitHub private vulnerability reporting으로 안내한다.

## Clean Export Procedure

1. private repo 밖에 fresh directory를 만든다.
2. included paths만 복사한다.
3. export 안에서 pre-export checks를 실행한다.
4. 새 Git repository를 initialize한다.
5. clean snapshot을 initial public commit으로 commit한다.
6. public repository에 push한다.
7. GitHub community profile status를 확인한다.
8. public repository에서 CI가 pass하는지 확인한다.
9. real maintainers 또는 teams가 생긴 뒤 branch protection과 CODEOWNERS review settings를 확인한다.

## GitHub Repository Setup

Enable 또는 configure:

- Private vulnerability reporting, if available
- Branch protection for the default branch
- Required CI checks
- Dependabot or equivalent dependency update automation, when package manifests are introduced
- OpenSSF Scorecard workflow, once the public repository is stable

## Public Smoke Test

publication 이후 clean clone을 test한다.

```bash
git clone <public-repo-url> ghost-alice-public-smoke
cd ghost-alice-public-smoke
python3 scripts/validate_public_surfaces.py
bash install.sh --status
```

smoke test가 disposable account 또는 machine에서 의도적으로 실행되는 것이 아니라면 main development environment를 mutate하는 install commands를 실행하지 않는다.
