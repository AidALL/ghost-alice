# Skill Catalog Guide

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/skills.md)

The installable skill catalog source of truth is [skill-catalog/skills.json](../../skill-catalog/skills.json).

This page is only a pointer for contributors. It should not duplicate the current skill count, skill list, or routing metadata, because those values change in the catalog.

## Where To Look

- Public skill availability: [skill-catalog/skills.json](../../skill-catalog/skills.json)
- Session gate routing: [skill-catalog/session-gates.json](../../skill-catalog/session-gates.json)
- Skill authoring rules: [AGENTS.md](../../AGENTS.md)
- Compliance checklist: [official-docs/derived/skill-compliance-checklist.md](../../official-docs/derived/skill-compliance-checklist.md)

## Update Rule

When public skill availability changes, update `skill-catalog/skills.json` first, then rebuild or validate public surfaces:

```bash
python3 scripts/validate_public_surfaces.py
```
