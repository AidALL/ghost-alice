# Ghost-ALICE Codex Bootstrap

{{COMMON_GOVERNANCE}}

## Codex tools and installation

The current-session assistant uses the tools actually exposed by this host. A Claude `Skill` instruction means read or validly reuse the installed instruction body in Codex, then perform that workflow. Metadata alone is not execution. Claude `Task`, `TodoWrite`, and file/shell tool names map to currently available host capabilities; a configuration flag cannot supply a capability that the host does not expose.

Installed skills are under `~/.agents/skills/`; Claude-only `${CLAUDE_SKILL_DIR}` is not a Codex path. Resolve `scripts/` and `references/` from the selected installed skill. Native hook configuration is `~/.codex/hooks.json`; provenance coordinates come from the current hook receipt, not these default locations.

When hooks are unavailable, the current-session assistant performs the required intake, routing and audit steps manually for work. The current-session assistant does not claim native hook execution or invent a session receipt.
