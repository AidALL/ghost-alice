# Skill Catalog Guide

언어: [🇺🇸 English](../../reference/skills.md) | 🇰🇷 한국어

installable skill catalog의 source of truth는 [skill-catalog/skills.json](../../../skill-catalog/skills.json)이다.

이 문서는 contributor가 확인할 위치를 안내한다. 현재 skill 개수, skill 목록, routing metadata는 catalog에서 바뀌므로 이 문서가 중복 기록하지 않는다.

## 확인 위치

- 공개 skill 제공 상태: [skill-catalog/skills.json](../../../skill-catalog/skills.json)
- session gate routing: [skill-catalog/session-gates.json](../../../skill-catalog/session-gates.json)
- skill 작성 규칙: [AGENTS.md](../../../AGENTS.md)
- compliance checklist: [official-docs/derived/skill-compliance-checklist.md](../../../official-docs/derived/skill-compliance-checklist.md)

## 업데이트 규칙

공개 skill 제공 상태가 바뀌면 먼저 `skill-catalog/skills.json`을 수정한 뒤 public surface를 rebuild 또는 validate한다.

```bash
python3 scripts/validate_public_surfaces.py
```
