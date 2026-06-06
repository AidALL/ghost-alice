# Contributing To Ghost-ALICE OS

Thanks for helping improve Ghost-ALICE OS. This project is an agent governance operating layer, so contributions are reviewed for behavior, safety, documentation, and verification evidence.
## Contents

- [Scope](#scope)
- [Community Contact](#community-contact)
- [Feedback We Want Most](#feedback-we-want-most)
- [Before You Start](#before-you-start)
- [Development Setup](#development-setup)
- [Contribution License](#contribution-license)
- [Validation Commands](#validation-commands)
- [Pull Request Expectations](#pull-request-expectations)
- [Public Boundary Rules](#public-boundary-rules)
- [Skill Changes](#skill-changes)
- [Reporting Security Issues](#reporting-security-issues)


## Scope

Ghost-ALICE OS public contributions should fit one of these areas.

- Core governance skills and their tests
- Installer behavior for Claude Code and Codex
- Hook routing, audit logging, and session gate policy
- Public documentation, troubleshooting, and platform compatibility
- Public validation scripts and community health files
- Addon authoring documentation and the public addon manifest format (the addon skills themselves live in their own repositories)

Do not submit private company data, local user runtime state, production secrets, customer-specific skills, or tenant-only workflow content.

## Community Contact

- Maintainer: [@garlicvread](https://github.com/garlicvread)
- Public questions, bugs, feature requests, and workflow feedback should use GitHub Issues.
- Private project contact and private security fallback: `aidall_manager@aidall.tech`
- Personal email addresses are not monitored for this project.

## Feedback We Want Most

Ghost-ALICE OS is built around constraint-guided agent work. The most useful feedback helps preserve the governance contract while improving when, where, and how that contract is surfaced to users.

High-signal feedback includes:

- Reproducible cases where Ghost-ALICE OS prevented a real agent failure
- Reproducible cases where a vanilla agent failed but Ghost-ALICE OS helped
- Workflows where Ghost-ALICE OS blocked useful work, raised the wrong gate, or made recovery harder
- False positives or false negatives in session intent, jailbreak detection, task routing, boundary contracts, completion checks, or io-trace behavior
- Visibility profile ideas for `strict`, `dynamic`, and `minimal` modes that keep core gate execution intact
- Cases where governance output was too verbose, too hidden, or poorly timed for the task risk
- Missing gates for high-risk workflows, especially credentials, external side effects, legal or financial claims, code changes, and release steps
- Documentation gaps that make the operating model, recovery path, or verification burden unclear

Lower-priority feedback includes:

- Requests to remove core safety gates entirely without a concrete failing workflow
- Pure "make it shorter" feedback without a task transcript, risk class, or recovery-cost example
- Style-only preferences that do not improve reliability, auditability, recovery, or user control
- Suggestions that require storing raw prompts, secrets, private runtime state, or customer-specific workflow data

When reporting a workflow case, include what you were trying to do, the platform, the active visibility profile if known, the gate or hook surface involved, what happened, what you expected, and the smallest reproducible transcript or fixture that avoids private data.

## Before You Start

1. Read [README.md](./README.md) for the project model.
2. Read [AGENTS.md](./AGENTS.md) for the repository operating contract.
3. Check [docs/getting-started/installation.md](./docs/getting-started/installation.md) for installer behavior and platform paths.
4. For public surface changes, run the validation commands listed below before opening a pull request.

## Development Setup

```bash
git clone https://github.com/AidALL/ghost-alice.git
cd ghost-alice
python3 --version
```

The installer requires Python 3.11 or newer.

To install locally for one platform:

```bash
bash install.sh --platform codex
```

To inspect the install state:

```bash
bash install.sh --doctor
bash install.sh --status
```

## Contribution License

Unless you clearly mark a submission as "Not a Contribution", contributions
submitted through issues, pull requests, discussions, or other project-managed
channels are submitted for inclusion in Ghost-ALICE OS under the Apache License,
Version 2.0.

Do not submit code, documentation, images, datasets, generated content, or
reference material unless you have the right to contribute it under terms
compatible with this project. If your change includes third-party material,
identify the source, license, copyright notice, and local path in the pull
request.

This project does not currently require a separate CLA or DCO sign-off. If that
policy changes, it must be documented before maintainers require it for new
pull requests.

## Validation Commands

Run the focused checks that match your change. For a public-readiness or release pull request, run the full public validation set.

```bash
python3 scripts/validate_public_surfaces.py
python3 scripts/check_skill_gate_contract.py
python3 -m unittest discover -s _shared -p 'test_*.py'
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

For installer and platform changes, also run:

```bash
python3 scripts/run_installer_compat_tests.py
python3 scripts/validate_platform_adapters.py
```

Windows installer changes should include PowerShell-oriented validation where possible.

```powershell
.\install.ps1 -Doctor
```

## Pull Request Expectations

Each pull request should include:

- What changed
- Why the change is needed
- Which user-facing surfaces are affected
- Which files or directories were intentionally not changed
- Verification commands and results
- Any third-party material, generated content, or provenance notes involved
- Any remaining risk or follow-up work

For governance behavior changes, include the relevant gate or hook surface, such as `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, or `SessionStart`.

Keep pull requests focused. Separate unrelated documentation, installer, hook,
and skill behavior changes unless the coupling is part of the problem being
fixed.

## Public Boundary Rules

Do not include:

- API keys, tokens, passwords, private keys, cookies, or local credential files
- `~/.ghost-alice/secrets.env` or real secret values from any source
- Local install logs, `.tmp/`, pending merge queues, or user-specific runtime state
- Customer, grant, company, or tenant data that is not intentionally public
- Private addon skills or private design overlays unless they are explicitly approved for release

Document public examples with placeholders. Avoid real usernames, machine paths, and organization-private URLs unless they are already public project URLs.

## Skill Changes

When adding or modifying a skill:

1. Keep `SKILL.md` focused and under the project disclosure limit.
2. Put long details in `references/`.
3. Put executable checks in `scripts/`.
4. Run the skill compliance checklist referenced from [README.md](./README.md).
5. Update `skill-catalog/skills.json` and public docs when the public surface changes.

For an addon, which is a third-party skill bundle installed with `--addon-source`, author it in your own repository against the `addons-manifest.json` and `addon.json` format, keep its skill names from colliding with core skills, and follow the [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring). Do not submit private addon skills into this public repository.

## Reporting Security Issues

Do not report vulnerabilities through public issues. Follow [SECURITY.md](./SECURITY.md).
