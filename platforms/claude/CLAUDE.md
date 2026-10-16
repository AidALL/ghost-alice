# Ghost-ALICE Claude Bootstrap

{{COMMON_GOVERNANCE}}

## Claude tools and installation

The current-session assistant loads required skills through Claude's visible `Skill` tool and follows their bodies.

Installed skills are under `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/`; native hooks are in `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`. The current-session assistant uses the current hook receipt for ledger/session identity and never derives write authority from these default paths.

When hooks are disabled, unavailable, or not trusted, the current-session assistant does not claim native intake, routing, Stop or audit observations. For work, the current-session assistant performs intake, routing, required Skill calls and internal auditing manually.
