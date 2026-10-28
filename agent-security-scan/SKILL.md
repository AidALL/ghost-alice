---
name: agent-security-scan
description: "Scans settings, hooks, skills, Model Context Protocol config and credential files for exposure, remote-fetch and shell side-effect risks after any of them or governance logic changes, or when the user asks to inspect suspicious commands."
compatibility:
  - "Python 3.11+ standard library"
---

# Agent Security Scan

The current-session assistant uses this skill for a report-only static review of agent settings, hooks, skill text and Model Context Protocol configuration. The default scanner reads local files and reports credential exposure, remote fetch/instruction surfaces, shell side effects, package-manager use and broad execution/protocol exposure. External security runtimes are optional.

## Scope and trigger

Apply the scan after relevant hooks, skills, server configuration, credential surfaces or external governance logic change, or when the user requests inspection of suspicious commands. The current-session assistant selects the authorized repository and home targets before invocation; this inventory does not expand the user's scope.

| Surface | Candidate paths |
| --- | --- |
| Claude Code | `~/.claude/settings.json`, `~/.claude/skills/*/SKILL.md` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, `~/.agents/skills/*/SKILL.md` |
| Repository | `<root>/*/SKILL.md`, `<root>/.mcp.json`, `<root>/.env` |
| Home | `~/.mcp.json`, `~/.env` |

## Procedure

1. The current-session assistant establishes the repository root and home candidates from authorized context.
2. When both the repository and home candidate sets are authorized, it invokes `python3 <loaded-skill-directory>/scripts/scan_agent_security_surface.py --root <absolute-repo> --home <absolute-home> --json` through the exposed shell tool. The bundled command scans both sets and has no path allowlist. For a narrower request, inspect only the named allowed files through the host's readers and report that limited coverage; do not broaden the request or invent an empty home path to imitate a scope flag.
3. It reads the exit status and returned JSON `root`, `home`, `finding_count` and `findings`, then each finding's severity, rule and mitigation. The scanner's root skill enumeration matches immediate `*/SKILL.md`; do not claim recursive family coverage from that command. An unavailable observation leaves that coverage unresolved.
4. It reports `mitigation=reject`, critical and high findings as blockers requiring a separately authorized fix workflow. Scanner output alone does not authorize deletion, edits or disabling servers.

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
- `medium - package manager or broad Model Context Protocol exposure`
- `medium - remote instruction surface`
- `low - unclear provenance or ownership`
- `low - hygiene or configuration issue`

## Warnings

The current-session assistant reports paths, rules and redacted messages, never raw secrets. The default scan makes no network call and applies no automatic fix. This skill inspects execution surfaces; adversarial-verification separately evaluates claims and evidence.
