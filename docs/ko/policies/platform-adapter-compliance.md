# Platform Adapter Compliance

언어: [🇺🇸 English](../../policies/platform-adapter-compliance.md) | 🇰🇷 한국어

이 contract는 Claude, Codex, terminal-only adapters가 실제로 지원하는 surface를 과장 없이 기록한다.
## Contents

- [Principles](#principles)
- [Adapter Records](#adapter-records)
- [States](#states)
- [Required Adapters](#required-adapters)
- [Verification](#verification)


## Principles

- hook semantics가 runtime smoke로 검증되지 않은 platform은 `instruction-backed` 상태로 둔다.
- `instruction-backed`는 policy text, skill placement, installer onramp가 있다는 뜻이다. direct runtime hook equivalence를 의미하지 않는다.
- installer가 Codex hook file을 작성하더라도, 그 event semantics를 gate completion evidence로 쓰려면 먼저 smoke evidence가 필요하다.
- `terminal-only`는 hookless fallback으로 취급한다.

## Adapter Records

`skill-catalog/platform-adapters.json`의 각 record는 다음 field를 가진다.

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

- `native`: platform runtime이 installer-managed hooks와 skill surfaces를 직접 지원한다.
- `instruction-backed`: instructions, skill placement, adapter config는 있지만 hook/event semantics에는 여전히 smoke evidence가 필요하다.
- `terminal-only`: humans가 project policy와 skill files를 fallback으로 따라야 한다.

## Required Adapters

- `claude`
- `codex`
- `terminal-only`

## Verification

```bash
python3 scripts/validate_platform_adapters.py
```

validator는 required fields, required adapter ids, date formats, source doc existence, Codex native claims absence, hook evidence/fallback records를 검사한다.
