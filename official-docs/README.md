# official-docs structure guide

This directory holds only public contributor-facing governance documentation.
The public distribution does not include verbatim snapshots of external documents, private integration analysis, internal planning documents,
or long-form philosophy notes.

## External Source Policy

- External documents are referenced by link and a short citation rather than by copying the original text.
- The public release does not include any snapshot whose redistribution rights have not been confirmed.
- A document that analyzes an external original is rewritten as project-owned commentary, and a source locator is left behind.

## derived/

Place here only project-owned procedure documents that public contributors must actually follow.

- This is an editing target. Apply all of the AGENTS.md rules.
- Do not place external-original analysis, private integration review, or internal roadmaps in this directory.
- Files currently included:
  - `skill-compliance-checklist.md`: the skill official-spec compliance verification checklist

## Planning document entry point

Upcoming work starts from `../docs/plans/README.md`. `official-docs/derived/` holds only public contributor procedure documents.

## New document classification criteria

When adding a new document, use the questions below to decide which layer it goes into.

- Does it store an external original as-is? It is not a public release target.
- Does it analyze, edit, or summarize external material, or include our opinion? Use public wiki, issue, or private notes.
- Is it used as a Ghost-ALICE OS runtime, verification, or gate rule? Use AGENTS.md, SKILL.md, docs/policies/, or derived/ checklist.
- If it is ambiguous, do not include it in the public release
