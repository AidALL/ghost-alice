# 스킬 카탈로그 안내

[🇺🇸 English](../../reference/skills.md) | [🇰🇷 한국어](./skills.md)

설치 가능한 스킬 카탈로그는 [skill-catalog/skills.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/skills.json)이고, `scripts/build_catalog.py`가 각 스킬의 `SKILL.md` 머리말에서 이 파일을 생성합니다.

이 문서는 기여자가 확인할 자료의 위치만 안내합니다. 현재 스킬 수, 스킬 목록과 라우팅 메타데이터는 카탈로그에서 변경되므로 이 문서에 중복 기록하지 않습니다.

## 확인 위치

- 공개 스킬 제공 상태: [skill-catalog/skills.json](https://github.com/AidALL/ghost-alice/blob/main/skill-catalog/skills.json)
- 스킬 작성 규칙: [공통 거버넌스](../../../platforms/common-governance.md)
- 준수 점검표: [official-docs/derived/skill-compliance-checklist.md](https://github.com/AidALL/ghost-alice/blob/main/official-docs/derived/skill-compliance-checklist.md)

## 업데이트 규칙

관리자는 공개 스킬을 추가하거나 삭제할 때 `SKILL.md`가 있는 스킬 디렉터리를 추가하거나 삭제하고, 카탈로그 항목을 변경할 때 그 `SKILL.md` 머리말을 수정합니다. 그다음 `python3 scripts/build_catalog.py`로 카탈로그를 다시 생성하고 다음 명령으로 공개 안내를 검증합니다.

```bash
python3 scripts/validate_public_surfaces.py
```
