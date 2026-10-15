# Skill Catalog Guide

Language: [🇺🇸 English](../../reference/skills.md) | 🇰🇷 한국어

설치 가능한 스킬 목록의 기준 자료는 [skill-catalog/skills.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/skills.json)입니다.

이 문서는 기여자가 확인할 자료의 위치를 안내합니다. 관리자는 현재 스킬 수, 목록과 라우팅 메타데이터를 카탈로그에 기록하며, 이 문서에는 중복 기록하지 않습니다.

## 확인 위치

- 공개 스킬 제공 상태: [skill-catalog/skills.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/skills.json)
- 세션 검사 절차의 라우팅: [skill-catalog/session-gates.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/session-gates.json)
- 스킬 작성 규칙: [공통 거버넌스](../../../platforms/common-governance.md)
- 준수 점검표: [official-docs/derived/skill-compliance-checklist.md](https://github.com/AidALL/ghost-alice/blob/main/official-docs/derived/skill-compliance-checklist.md)

## 업데이트 규칙

관리자는 공개 스킬 제공 상태가 바뀌면 먼저 `skill-catalog/skills.json`을 수정한 뒤 공개 안내를 다시 생성하거나 다음 명령으로 검증합니다.

```bash
python3 scripts/validate_public_surfaces.py
```
