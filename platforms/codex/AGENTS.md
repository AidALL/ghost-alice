# Ghost-ALICE Codex Bootstrap

{{COMMON_GOVERNANCE}}

## Codex tools and installation

The current-session assistant uses the tools actually exposed by this host. A Claude `Skill` instruction means read or validly reuse the installed instruction body in Codex, then perform that workflow. Metadata alone is not execution. Claude `Task`, `TodoWrite`, and file/shell tool names map to currently available host capabilities; a configuration flag cannot supply a capability that the host does not expose.

Installed skills are under `~/.agents/skills/`; Claude-only `${CLAUDE_SKILL_DIR}` is not a Codex path. Resolve `scripts/` and `references/` from the selected installed skill. Native hook configuration is `~/.codex/hooks.json`.

When this session's context carries no Ghost-ALICE session receipt, Ghost-ALICE cannot record this session. The response model then follows the contract without writing ledger records, tells the user once that this session is not recorded, does not invent a receipt, and does not claim intake, routing, Stop or audit records.
