---
name: agent-security-scan
description: "Use for report-only static scans of settings, hooks, skills, Model Context Protocol config, credential surfaces, remote fetches, shell side effects, and broad exposure risks. Do not print raw secrets or apply automatic fixes."
compatibility:
  - "Python 3.11+ standard library"
---

# agent-security-scan

agent-security-scan performs report-only static review of agent execution
surfaces. It scans settings, hooks, skills, Model Context Protocol
configuration, and skill text for credential, remote fetch, shell side-effect,
package-manager, protocol exposure, and broad execution risks.

The default implementation reads local files only. External security runtimes
such as AgentShield are optional dependencies, not required behavior.
## Contents

- [Scan Targets](#scan-targets)
- [When To Use](#when-to-use)
- [Procedure](#procedure)
- [Severity Values](#severity-values)
- [Output Format](#output-format)
- [Warnings](#warnings)


## Scan Targets

- Claude Code: `~/.claude/settings.json`, `~/.claude/skills/*/SKILL.md`
- Codex: `~/.codex/hooks.json`, `~/.codex/config.toml`, `~/.agents/skills/*/SKILL.md`
- Repo: `<root>/*/SKILL.md`, `<root>/.mcp.json`, `<root>/.env`
- HOME: `~/.mcp.json`, `~/.env`

## When To Use

- After adding hooks, skills, Model Context Protocol servers, or credential surfaces.
- After absorbing external agent governance logic.
- When `settings.json`, `hooks.json`, `.mcp.json`, or `SKILL.md` may contain
  suspicious execution commands, remote fetches, or credential access.

## Procedure

1. Choose the repository root and HOME candidates.
2. Run `scripts/scan_agent_security_surface.py --root <repo> --home <home> --json`.
3. Inspect each finding's `severity`, `rule`, and `mitigation`.
4. Treat `mitigation=reject`, `critical - ...`, and `high - ...` findings as
   blockers that need a separate fix plan.
5. Do not delete or modify anything automatically from scanner output alone.

## Severity Values

- `critical - credential exposure or secret access`
- `high - command can run without protective gate`
- `high - write, install, permission, or destructive side effect`
- `medium - package manager or broad Model Context Protocol exposure`
- `medium - remote instruction surface`
- `low - unclear provenance or ownership`
- `low - hygiene or configuration issue`

## Output Format

```json
{
  "findings": [
    {
      "severity": "high - command can run without protective gate",
      "rule": "unprofiled-shell-command",
      "path": "/path/settings.json",
      "message": "hook command executes through shell without Ghost-ALICE profile gate",
      "mitigation": "mitigate"
    }
  ]
}
```

## Warnings

- Never print raw secret values. Report only paths, rules, and redacted messages.
- Do not apply automatic fixes, deletes, or Model Context Protocol disables.
- agent-security-scan is not adversarial-verification. It reviews the
  Ghost-ALICE execution surface, not the truth of claims and evidence.
- Do not call external network resources during the default scan.
