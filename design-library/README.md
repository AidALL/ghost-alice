---
name: design-library
description: A public design-system catalog referenced when authoring and styling visual deliverables (HTML, SVG, flow diagrams, schematics, concept maps, thumbnails, UI mockups, infographics, posters, web pages, dashboards, presentation slides). Use color palettes, typography, component styles, shadows, spacing, radius, depth, and responsive principles as references. Use when producing diagrams, flowcharts, HTML schematics, style guides, visualizations, UI drafts, document schematics, infographics, and presentation styling.
type: reference-library
---

# design-library

A public design-system catalog referenced when authoring visual deliverables. It is a reference-library that helps make styling decisions at runtime, so it does not generate code directly. Instead, read the relevant brand `*.md` in the catalog and use it as the styling basis.
## Contents

- [Catalog location](#catalog-location)
- [Usage procedure](#usage-procedure)
- [Catalog examples (not exhaustive)](#catalog-examples-not-exhaustive)
- [adversarial-verification](#adversarial-verification)
- [task-router integration](#task-router-integration)
- [References](#references)


## Catalog location

- `~/ghost-alice/design-library/catalog/<slug>.md`: per-brand design-system specifications (62 entries).
- `~/ghost-alice/design-library/normalized/<slug>/`: SSOT-converted versions (brand-neutralized) produced by the addon-provided design-library-normalizer.
- `~/ghost-alice/design-library/manifest.json`: catalog index.

## Usage procedure

1. Detect a visual-deliverable request (diagram, HTML, SVG, card, UI, infographic, and so on).
2. Confirm the user's preferred brand. If one is specified, read that `catalog/<brand>.md`.
3. If none is specified, recommend one from context (presentation or report -> calm corporate style / developer dashboard -> dense product style / data-dense UI -> system style with strong tables and contrast).
4. Use the sections "1. Visual Theme" through "9. Agent Prompt Guide" of the catalog document as the styling basis.
5. Do not expose the brand name directly inside the generated deliverable. Borrow only the styling principles.
6. When there is a brand-replication risk (public distribution or commercial documents), convert to SSOT with the addon-provided design-library-normalizer before use.

## Catalog examples (not exhaustive)

| slug | character | representative use |
|---|---|---|
| airbnb | warm, photo-centric | presentation schematics, card UI |
| apple | minimal, whitespace-focused | product introductions, presentations |
| stripe | data-dense, refined | API docs, dashboards |
| linear.app | developer-oriented, black background | issue trackers, timelines |
| vercel | black background, monospace | deployment interfaces, log views |
| notion | writing tool, neutral | document editors, workspaces |
| anthropic-claude | warm cream color | AI conversation UI |
| tesla | black background, futuristic | car and robot product introductions |
| spotify | dark background, neon | audio and media players |
| stripe, supabase, sentry | data-dense | dashboards |

Check the full list by running `ls` on the `catalog/` directory.

## adversarial-verification

If you suspect the generated visual deliverable was "replicated to the point of being confused with a specific brand", verify it with adversarial-verification. Apply this without exception to public distribution and commercial documents.

## task-router integration

If task-router §1.1 detected a "visual or design signal", add this skill to the output-skill candidates. It is a matching rule is registered in the output-skill table in §1.2.

## References

- references/usage-walkthrough.md: examples of selecting and applying a brand from the catalog (to be written).
- addon-provided `design-library-normalizer`. This means the brand-neutral SSOT conversion pipeline.
