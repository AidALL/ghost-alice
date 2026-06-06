# Platform Adapter Compliance

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/platform-adapter-compliance.md)

This contract records, without exaggeration, which surfaces are actually supported by the Claude, Codex, and terminal-only adapters.
## Contents

- [Principles](#principles)
- [Adapter Records](#adapter-records)
- [States](#states)
- [Required Adapters](#required-adapters)
- [Verification](#verification)


## Principles

- A platform whose hook semantics have not been verified by runtime smoke stays `instruction-backed`.
- `instruction-backed` means policy text, skill placement, and installer onramp exist. It does not mean direct runtime hook equivalence.
- Even when Codex hook files are written by the installer, their event semantics require smoke evidence before they can be used as gate completion evidence.
- `terminal-only` is treated as a hookless fallback.

## Adapter Records

Each record in `skill-catalog/platform-adapters.json` has these fields:

- `id`
- `state`
- `supported_assets`
- `unsupported_surfaces`
- `install_or_onramp`
- `verification_commands`
- `risk_notes`
- `last_verified_at`
- `owner`
- `source_docs`

## States

- `native`: the platform runtime directly supports installer-managed hooks and skill surfaces.
- `instruction-backed`: instructions, skill placement, and adapter config exist, but hook/event semantics still require smoke evidence.
- `terminal-only`: humans must follow project policy and skill files as the fallback.

## Required Adapters

- `claude`
- `codex`
- `terminal-only`

## Verification

```bash
python3 scripts/validate_platform_adapters.py
```

The validator checks required fields, required adapter ids, date formats, source doc existence, absence of Codex native claims, and hook evidence/fallback records.
