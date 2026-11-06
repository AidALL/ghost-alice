# Skill Catalog Guide

[🇺🇸 English](./skills.md) | [🇰🇷 한국어](../ko/reference/skills.md)

The installable skill catalog is [skill-catalog/skills.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/skills.json); `scripts/build_catalog.py` generates it from each skill's `SKILL.md` frontmatter.

This page is only a pointer for contributors. It should not duplicate the current skill count, skill list, or routing metadata, because those values change in the catalog.

## Where To Look

- Public skill availability: [skill-catalog/skills.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/skills.json)
- Skill authoring rules: [Common governance](../../platforms/common-governance.md)
- Compliance checklist: [official-docs/derived/skill-compliance-checklist.md](https://github.com/AidALL/ghost-alice/blob/main/official-docs/derived/skill-compliance-checklist.md)

## Update Rule

To add or remove a public skill, add or remove its directory with its `SKILL.md`; to change a catalog entry, edit that `SKILL.md` frontmatter. Then regenerate the catalog with `python3 scripts/build_catalog.py` and validate public surfaces:

```bash
python3 scripts/validate_public_surfaces.py
```
