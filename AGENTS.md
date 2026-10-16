# Ghost-ALICE Project Instructions

Common governance comes from the current host's installed common contract. Use the host loader and exact current-session receipt; a repository contract source does not replace that execution authority. Codex loads its contract from `~/.codex/ghost-alice-governance.md`; Claude Code receives its installed `~/.claude/CLAUDE.md` contract.

For development, `platforms/common-governance.md` owns the shared contract source. Platform templates add only host-specific rules; the installer composes that source into the installed contract. Inspect these source files only when the authorized development task needs them.

Project work follows the user-selected source owner. Preserve existing changes and distinguish source modification, verification, installation and actual native adoption.

## Development rules

- Keep `platforms/common-governance.md` host-neutral. Templates under `platforms/<host>/` carry only host mechanics such as skill loading, installed paths, hook stages and hookless fallback; they do not restate common rules.
- Name the actor, action, input, output, recipient and condition. Each owning procedure describes its handoff once; other documents refer to it. Write current behavior without historical alternatives. Keep paired English/Korean documents and diagrams aligned with callers.
- Before editing a skill, follow `official-docs/derived/skill-compliance-checklist.md`, phases 1–5. Keep the skill entry short, detailed procedures in references, and executables in scripts.
