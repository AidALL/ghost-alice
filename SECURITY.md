# Security Policy

Ghost-ALICE OS installs skills, hooks, and agent governance files into local developer environments. Security reports are taken seriously because bugs can affect user intent handling, local configuration, audit logs, and secret boundaries.
## Contents

- [Reporting A Vulnerability](#reporting-a-vulnerability)
- [Sensitive Surfaces](#sensitive-surfaces)
- [Non-Security Issues](#non-security-issues)
- [Disclosure Expectations](#disclosure-expectations)
- [Secret Handling](#secret-handling)


## Reporting A Vulnerability

Do not open a public issue for a security vulnerability.

Use GitHub private vulnerability reporting when it is enabled for the public repository. If private reporting is not available, email `aidall_manager@aidall.tech` with the subject prefix `[ghost-alice security]` and request a private disclosure channel before sending sensitive details.

Include as much of the following as possible:

- Affected platform: Claude Code, Codex, or installer-only
- Affected operating system and shell
- Affected file or hook surface
- Reproduction steps
- Expected behavior and observed behavior
- Whether secrets, local paths, or user intent logs may be exposed
- Minimal proof of concept, with secrets removed

## Sensitive Surfaces

Please treat these areas as security-sensitive:

- Hook execution and hook ordering
- `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, and `SessionStart` behavior
- Secret helpers under `_shared/secrets/`
- Installer writes to `~/.claude`, `~/.codex`, `~/.agents`, and `~/.ghost-alice`
- Session intent ledgers and governance event logs
- Pending merge manifests and install-state manifests
- Any path that can contain user prompts, local credentials, or private project metadata

## Non-Security Issues

Use public issues for normal bugs, documentation improvements, platform compatibility reports, and feature requests.

Examples that are usually not security vulnerabilities:

- A typo in public documentation
- A validation script failing on a new unsupported shell
- A missing public docs link
- A confusing but non-sensitive installer message

## Disclosure Expectations

The maintainers will make a best effort to acknowledge reports, investigate affected versions, and coordinate a fix before public disclosure. Do not publish exploit details until the maintainers have had a reasonable chance to respond.

## Secret Handling

Never include real credentials in reports. Replace secrets with placeholders such as `REDACTED_TOKEN` and include only the minimum path or environment context needed to reproduce the issue.
