# Third-Party Notices

Ghost-ALICE OS project-owned source code and documentation are licensed under
the Apache License, Version 2.0. See `LICENSE`.

This file records bundled third-party reference material and provenance that is
not assumed to be project-owned Apache-2.0 content unless explicitly stated.
## Contents

- [Bundled Reference Material](#bundled-reference-material)
  - [`design-library/`](#design-library)
- [Project-Owned Implementations With External Provenance](#project-owned-implementations-with-external-provenance)
  - [`coding-convention/`](#coding-convention)
- [Maintainer Checklist](#maintainer-checklist)


## Bundled Reference Material

### `design-library/`

- Source package: `getdesign`
- Local source metadata: `design-library/.source-meta.json`
- Upstream repository: https://github.com/VoltAgent/awesome-design-md
- Package source: https://registry.npmjs.org/getdesign/
- License: MIT License
- Copyright notice from upstream: Copyright (c) 2026 VoltAgent

The `design-library/catalog/` entries are bundled reference material derived
from the upstream package listed above. Keep the upstream copyright and MIT
permission notice available with public distributions that include this
directory.

MIT License notice:

```text
MIT License

Copyright (c) 2026 VoltAgent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

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
