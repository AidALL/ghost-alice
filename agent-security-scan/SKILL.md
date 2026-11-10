---
name: agent-security-scan
description: "Scans the host's settings, hooks, skills, Model Context Protocol config and credential files for exposed secrets, remote fetches and side-effecting shell commands, after any of them changes or governance rules from another project are adopted, or when the human user asks to check them."
compatibility:
  - "Python 3.11+ standard library"
---

# Agent Security Scan

The response model uses this skill for a report-only static review of host settings, hooks, skill text and Model Context Protocol configuration. The default scanner reads local files and reports exposed credentials, remote fetches and instructions, shell side effects, package-manager use and broad execution or protocol exposure. External security runtimes are optional.

## Scope and trigger

Apply the scan after relevant hooks, skills, server configuration or credential files change, after governance rules from another project are adopted, or when the human user asks to inspect suspicious commands. The response model selects the repository and home targets that the human user authorized before running the scanner; the table below does not expand the human user's scope.

| Surface | Candidate paths |
| --- | --- |
| Claude Code | `~/.claude/settings.json`, `~/.claude/skills/*/SKILL.md` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, `~/.agents/skills/*/SKILL.md` |
| Repository | `<root>/*/SKILL.md`, `<root>/.mcp.json`, `<root>/.env` |
| Home | `~/.mcp.json`, `~/.env` |

## Procedure

1. The response model establishes the repository root and home candidates from the targets that the human user authorized.
2. When the human user authorized both the repository and home candidate sets, the response model invokes `python3 <loaded-skill-directory>/scripts/scan_agent_security_surface.py --root <absolute-repo> --home <absolute-home> --json` through the host's shell tool. The bundled command scans both sets and has no path allowlist. When the human user requests a narrower scan, the response model reads only the named allowed files through the host's file tools and reports that limited coverage; it does not broaden what the human user requested or invent an empty home path to imitate a scope flag.
3. The response model reads the exit status and the returned JSON `root`, `home`, `finding_count`, `findings` and `coverage`, then each finding's severity, rule and mitigation. It reports each `unobserved` coverage entry and its read or parse failure as unresolved coverage. The scanner lists skills under the root by matching immediate `*/SKILL.md`; the response model does not claim that it covers skills nested in a family folder.
4. It reports `mitigation=reject`, critical and high findings as blockers requiring a fix workflow that the human user separately authorized. Scanner output alone does not authorize deletion, edits or disabling servers.

## Output

```json
{
  "findings": [{
    "severity": "high - command can run without protective gate",
    "rule": "unprofiled-shell-command",
    "path": "/path/settings.json",
    "message": "hook command executes through shell without Ghost-ALICE profile gate",
    "mitigation": "mitigate"
  }]
}
```

The scanner's severity values are:

- `critical - credential exposure or secret access`
- `high - command can run without protective gate`
- `high - write, install, permission, or destructive side effect`
- `medium - package manager or broad MCP exposure`
- `medium - remote instruction surface`
- `low - unclear provenance or ownership`
- `low - hygiene or configuration issue`

## Warnings

The response model reports paths, rules and redacted messages, never raw secrets. The default scan makes no network call and applies no automatic fix. This skill inspects settings, hooks and skill text that can run commands; adversarial-verification separately evaluates claims and evidence.
