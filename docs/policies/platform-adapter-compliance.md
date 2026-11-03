# Platform Adapter Compliance

[🇺🇸 English](./platform-adapter-compliance.md) | [🇰🇷 한국어](../ko/policies/platform-adapter-compliance.md)

Under this contract, `skill-catalog/platform-adapters.json` records, without exaggeration, the surfaces that the Claude, Codex, and terminal-only adapters actually support and the verification state of that support.
## Contents

- [Principles](#principles)
- [Adapter Records](#adapter-records)
- [States](#states)
- [Required Adapters](#required-adapters)
- [Verification](#verification)


## Principles

- A platform whose hook semantics have not been verified by runtime smoke stays `instruction-backed`.
- Even when Codex hook files are written by the installer, their event semantics require smoke evidence before they can be used as gate completion evidence.

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
- `instruction-backed`: instructions, skill placement, and installer onramp exist, but hook/event semantics still require smoke evidence.
- `terminal-only`: humans must follow project policy and skill files as the fallback.

## Required Adapters

- `claude`
- `codex`
- `terminal-only`

## Verification

```bash
python3 scripts/validate_platform_adapters.py
```

The validator checks required fields and adapter ids, date formats and the existence of source documents. It rejects a `native` state for Codex, requires the text of Codex's `unsupported_surfaces` and `risk_notes` to contain `hook` and one of `evidence`, `runtime` or `smoke`, and to contain `event` and `firing proof`, and requires Claude's `supported_assets` to contain both `repo-local` and `installed-global`.
