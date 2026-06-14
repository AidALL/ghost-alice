# Third-Party Notices

Ghost-ALICE OS project-owned source code and documentation are licensed under
the Apache License, Version 2.0. See `LICENSE`.

This file records bundled third-party reference material and provenance that is
not assumed to be project-owned Apache-2.0 content unless explicitly stated.
## Contents

- [Bundled Reference Material](#bundled-reference-material)
- [Project-Owned Implementations With External Provenance](#project-owned-implementations-with-external-provenance)
  - [`coding-convention/`](#coding-convention)
- [Maintainer Checklist](#maintainer-checklist)


## Bundled Reference Material

The core repository ships no bundled third-party reference material. If
reference data is added, record its source URL, license,
copyright notice, and local path here for release readiness.

## Project-Owned Implementations With External Provenance

### `coding-convention/`

The `coding-convention/` skill family is a Ghost-ALICE OS project-owned
implementation licensed with the project under Apache-2.0. Its governance
patterns, tool mappings, skill-authoring rules, and agent workflow conventions
were informed by and adapted from external public materials and prior art.

Known local provenance anchors include:

- `coding-convention/writing-skills/references/anthropic-best-practices.md`
- `coding-convention/using-coding-convention/references/codex-tools.md`
- `coding-convention/using-coding-convention/references/copilot-tools.md`

Do not assume that copied, translated, or closely adapted upstream documentation
inside these references is relicensed as project-owned Apache-2.0 material.
Public release docs should keep only original Ghost-ALICE commentary with short
citations or recorded source/license decisions.

## Maintainer Checklist

- Keep this file in sync when adding bundled external reference material.
- Keep source URLs and local locators specific enough for a future license audit.
- Prefer linking to upstream documentation over copying long upstream text.
