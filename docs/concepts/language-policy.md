# Language Policy

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/concepts/language-policy.md)

Ghost-ALICE OS uses English as the default repository entry language and maintains Korean counterparts for reader-facing documentation.

English remains the primary coordination language for code, schemas, control tokens, issue templates, release surfaces, and public contributor workflows. Korean is maintained as a paired reader-facing documentation path for Korean reviewers and contributors.

## Rules

- Keep English as the default entry path for the repository.
- Maintain Korean counterparts under `docs/ko/` for reader-facing repository documentation.
- Keep each paired document connected with a language switch near the top.
- In English documents, link internally to English documents by default.
- In Korean documents, link internally to Korean counterparts when a counterpart exists.
- Keep runtime contracts and executable behavior in the main repository.
- Use the Wiki for long-form paired explanation pages that need to remain readable outside a blocked local checkout.

## Literal Tokens

Keep these tokens literal across languages:

- file paths and directory names
- commands, flags, and environment variables
- JSON, TOML, YAML, and Markdown field names
- enum values and schema literals
- hook names and skill names
- platform names such as Codex and Claude Code

Translate the surrounding explanation, not the token.

## Drift Control

Update the English default document first, then update the Korean counterpart in the same change when user-facing meaning changes. Remove stale partial pages rather than preserving misleading duplicates. If a document intentionally has no Korean counterpart, explain the exception from the nearest documentation index.
