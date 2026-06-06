# Tool Output Semantics

언어: [🇺🇸 English](../../policies/tool-output-semantics.md) | 🇰🇷 한국어

이 문서는 human-readable label과 machine-readable field를 분리하는 minimum contract다. 넓은 status taxonomy를 새로 만들라는 허가가 아니다. 실제로 관찰된 misdiagnosis와 직접 이어지는 output surface만 추가한다.

## Contract

- machine-readable field가 control surface다.
- human label은 표시용일 뿐이다.
- 기존 compatibility field는 유지하되, new consumer는 compatibility field를 machine state로 해석하면 안 된다.
- user action이 달라질 때만 new field를 추가한다.

## Host Tool Input Schema Validation

tool argument의 형태는 Ghost-ALICE governance hook이 아니라 host harness가 소유한다. tool call이 required field를 빠뜨리면, host는 tool body가 시작되기 전에 submitted argument를 해당 tool JSON Schema에 대해 validate한다. `questions` 같은 required array가 없는 AskUserQuestion-style call은 실행 전 validation failure다. user UI는 열리지 않고 file이나 external side effect도 생길 수 없다. 복구는 같은 intent를 schema에 맞는 argument로 다시 호출하는 것이다.

Ghost-ALICE hook은 arbitrary serialized `tool_input` 내용, deny-list substring, 추론된 payload capability를 inspect해 이 검증을 중복하면 안 된다. governance boundary는 session intent, downstream gate state, completion evidence, IO trace, 문서화된 work contract다. malformed tool payload는 host schema validator의 책임으로 남긴다.

## `_shared/install_hooks.py`

`check_status_detail(platform_key)`는 structured hook state를 반환한다. 기존 `check_status(platform_key)`는 compatibility wrapper로 남아 `installed`, `missing`, `skipped`, `unsupported`만 반환한다.

| field | meaning | compatibility |
| --- | --- | --- |
| `status_token` | machine-readable hook state | new |
| `status_label` | localized human label | presentation only |
| `legacy_status` | existing installer status string | compatibility only |
| `details` | missing or drifted hook labels and reason metadata | new |
| `missing_reason` | `HOOK_MISSING`의 reason when known | new |
| `unsupported` | platform runtime이 hooks를 run할 수 없을 때 true | new |

Hook status tokens:

| status_token | trigger | user action |
| --- | --- | --- |
| `HOOK_INSTALLED_OK` | required marker와 expected command가 모두 match | none |
| `HOOK_INSTALLED_DRIFT` | required marker는 있지만 expected command가 다름 | reinstall hooks 또는 inspect local hook edits |
| `HOOK_CONFIG_DISABLED` | hook config가 있지만 runtime feature flag/config가 hooks를 disable | enable hook config |
| `HOOK_MISSING` | hook config file 또는 required marker가 없음 | install hooks 또는 restore missing marker |
| `HOOK_UNSUPPORTED` | platform runtime이 hooks를 run할 수 없음 | hookless fallback 사용 |
| `HOOK_PLATFORM_MISSING` | platform config directory가 없음 | skip platform 또는 install that platform |

`HOOK_MISSING`는 `missing_reason`을 사용한다.

| missing_reason | trigger |
| --- | --- |
| `config_file_absent` | hook config file이 없음 |
| `suite_incomplete` | hook config는 있지만 required marker 중 하나 이상이 없음 |

`HOOK_INSTALLED_DRIFT`는 `no hooks`가 아니다. Ghost-ALICE marker는 있지만 installed command가 current installer payload와 더 이상 맞지 않는다는 뜻이다.

## `agent-security-scan/scripts/scan_agent_security_surface.py`

`scan()`은 resolved path 기준으로 dedupe된 finding의 JSON list를 반환한다. `finding_count`는 compatibility field로 유지하되, new consumer는 `finding_count_semantics`도 함께 inspect해야 한다.

| field | meaning | compatibility |
| --- | --- | --- |
| `finding_count` | resolved path dedupe 이후 unique findings | existing |
| `finding_count_semantics` | `finding_count`의 literal semantics | new |
| `occurrence_count` | mirrors를 포함한 physical occurrence count, computed when available | optional |
