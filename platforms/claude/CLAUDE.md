# Ghost-ALICE Claude Bootstrap

{{COMMON_GOVERNANCE}}

## Claude tools and installation

The current-session assistant loads required skills through Claude's visible `Skill` tool and follows their bodies.

Installed skills are under `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/`; native hooks are in `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`. The current-session assistant uses the current hook receipt for ledger/session identity and never derives write authority from these default paths.

When this session's context carries no Ghost-ALICE session receipt, Ghost-ALICE cannot record this session. The response model then follows the contract without writing ledger records, tells the user once that this session is not recorded, does not invent a receipt, and does not claim intake, routing, Stop or audit records.
