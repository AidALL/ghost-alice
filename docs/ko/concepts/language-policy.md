# Language Policy

언어: [🇺🇸 English](../../concepts/language-policy.md) | 🇰🇷 한국어

Ghost-ALICE OS는 저장소의 기본 entry 언어로 English를 쓰고, reader가 보는 문서는 Korean counterpart를 함께 둔다.

code, schema, control token, issue template, release surface, contributor workflow를 조율하는 주 언어는 English다. Korean은 한국어 reviewer와 contributor를 위해 짝으로 두는 reader용 문서다.

## Rules

- repository의 default entry path는 English로 유지한다.
- reader-facing repository documentation은 `docs/ko/` 아래에 Korean counterpart를 유지한다.
- 각 paired document는 상단 근처에 language switch를 둔다.
- English 문서는 기본적으로 English 문서로 내부 링크한다.
- Korean 문서는 counterpart가 있을 때 Korean counterpart로 내부 링크한다.
- runtime contracts와 executable behavior는 main repository에 유지한다.
- local checkout이 막힌 상황에서도 읽어야 하는 긴 설명 문서는 Wiki에 둔다.

## Literal Tokens

다음 token은 언어가 달라도 literal로 유지한다.

- file paths and directory names
- commands, flags, and environment variables
- JSON, TOML, YAML, and Markdown field names
- enum values and schema literals
- hook names and skill names
- platform names such as Codex and Claude Code

token은 그대로 두고 주변 설명만 번역한다.

## Drift Control

English 기본 문서를 먼저 고치고, user-facing 의미가 바뀌면 같은 변경에서 Korean counterpart도 함께 고친다. 어중간하게 낡은 page는 남겨 두지 말고 지우거나 갱신한다. 일부러 Korean counterpart를 두지 않은 문서는 가장 가까운 documentation index에서 그 예외를 설명한다.
