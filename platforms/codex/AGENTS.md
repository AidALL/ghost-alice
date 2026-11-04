# Ghost-ALICE Codex Bootstrap

{{COMMON_GOVERNANCE}}

## Codex tools and installation

The response model uses the tools actually exposed by this host. A Claude `Skill` instruction means read or validly reuse the installed instruction body in Codex, then perform that workflow. Metadata alone is not execution. A configuration flag cannot supply a capability that the host does not expose.

Installed skills are under `~/.agents/skills/`; Claude-only `${CLAUDE_SKILL_DIR}` is not a Codex path. Resolve `scripts/` and `references/` from the selected installed skill. Native hook configuration is `~/.codex/hooks.json`.

Preserve the native `CODEX_THREAD_ID` binding of the session metadata's `session_id`; a mismatch is an error.
