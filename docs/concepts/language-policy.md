# Language Policy

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/concepts/language-policy.md)

Ghost-ALICE OS uses English as the default repository entry language and maintains Korean counterparts for reader-facing documentation.

English remains the primary coordination language for code, schemas, control tokens, issue templates, release surfaces, and public contributor workflows. Korean is maintained as a paired reader-facing documentation path for Korean reviewers and contributors.

## Rules

- Keep English as the default entry path for the repository.
- Maintain Korean counterparts under `docs/ko/` for reader-facing repository documentation.
- Keep each paired document connected with a language switch near the top. Write the switch label in the other language so that a reader who cannot read the page can still find it: English documents use `언어: 🇺🇸 English | [🇰🇷 한국어](…)` and Korean documents use `Language: [🇺🇸 English](…) | 🇰🇷 한국어`. The current language is plain text and the other language is the link.
- In English documents, link internally to English documents by default.
- In Korean documents, link internally to Korean counterparts when a counterpart exists.
- Keep runtime contracts and executable behavior in the main repository.
- Use the Wiki for long-form paired explanation pages that need to remain readable outside a blocked local checkout.
- Keep English Wiki pages at the Wiki root and Korean pages under `ko/`. Each folder has a sidebar in its own language that lists the numbered reading order. The Wiki has no footer, and each page's file name is its displayed title.

## Literal Tokens

Keep these tokens literal across languages:

- file paths and directory names
- commands, flags, and environment variables
- JSON, TOML, YAML, and Markdown field names
- enum values and schema literals
- hook names and skill names
- platform names such as Codex and Claude Code

Translate the surrounding explanation, not the token.

## Intentionally Unpaired Sources

Reader documentation, architecture, Wiki explanations and GitHub Pages remain paired in English and Korean. Runtime instruction bodies (`AGENTS.md`, platform bootstraps and `SKILL.md`), schemas, maintainer procedures, community policies, legal notices and provenance records intentionally retain one canonical English source. This includes `CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `.github/` templates and `official-docs/`. Their reader-facing explanations can link to the canonical source from either language; a second translated control or legal source is not implied.

## Drift Control

Update the English default document first, then update the Korean counterpart in the same change when user-facing meaning changes. Remove stale partial pages rather than preserving misleading duplicates. If a document intentionally has no Korean counterpart, explain the exception from the nearest documentation index.
