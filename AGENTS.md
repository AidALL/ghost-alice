# Ghost-ALICE Project Instructions

The common rules for this work come from the current host's installed common contract. Read it through the host's loader and use the current session metadata (the `[session-intent-receipt]` block) exactly as supplied; the contract's source in this repository does not replace the installed common contract as the authority for execution. Codex loads its contract from `~/.codex/ghost-alice-governance.md`; Claude Code receives its contract in `~/.claude/CLAUDE.md`.

For development, `platforms/common-governance.md` is the source of the common contract. The installer combines that source and a template under `platforms/<host>/` into the installed common contract. Read these source files only when the authorized development task needs them.

Project work follows the source file that the user selected as the owner. Preserve existing changes, and keep these results distinct: a source modification, a verification, an installation and the host's actual adoption of the change.

## Development rules

- Keep `platforms/common-governance.md` host-neutral. Templates under `platforms/<host>/` carry only host mechanics such as skill loading, installed paths and hook stages; they do not restate common rules. The exception is a rule that the response model needs before it has read the contract or when it cannot read it: the Codex bootstrap states the contract's direct-answer condition and its no-substitution prohibition in the contract's words, because the response model under Codex reads the contract only through a tool call.
- Name the actor, action, input, output, recipient and condition. Each owning procedure describes its handoff once; other documents refer to it. Write current behavior without historical alternatives. Keep paired English and Korean documents and their diagrams consistent with each other and with the documents and code that call them.
- Before editing a skill, follow `official-docs/derived/skill-compliance-checklist.md`, phases 1–5. Keep `SKILL.md` short, detailed procedures in `references/`, and executables in `scripts/`.
- Write each Markdown prose paragraph on one physical source line unless Markdown, syntax or protocol fields require separate lines.
