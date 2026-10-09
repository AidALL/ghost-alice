# Tool Output Semantics

언어: [🇺🇸 English](../../policies/tool-output-semantics.md) | 🇰🇷 한국어

이 문서는 human-readable label과 machine-readable field를 분리하는 minimum contract입니다. 넓은 status taxonomy를 새로 만들라는 허가가 아닙니다. 실제로 관찰된 misdiagnosis와 직접 이어지는 output surface만 추가합니다.

## Contract

- machine-readable field가 control surface입니다.
- human label은 표시용일 뿐입니다.
- 기존 compatibility field는 유지하되, new consumer는 compatibility field를 machine state로 해석하면 안 됩니다.
- user action이 달라질 때만 new field를 추가합니다.

## Host Tool Input Schema Validation

tool argument의 형태는 Ghost-ALICE governance hook이 아니라 host harness가 소유합니다. tool call이 required field를 빠뜨리면, host는 tool body가 시작되기 전에 submitted argument를 해당 tool JSON Schema에 대해 validate합니다. `questions` 같은 required array가 없는 AskUserQuestion-style call은 실행 전 validation failure입니다. user UI는 열리지 않고 file이나 external side effect도 생길 수 없습니다. 복구는 같은 intent를 schema에 맞는 argument로 다시 호출하는 것입니다.

Ghost-ALICE hook은 arbitrary serialized `tool_input` 내용, deny-list substring, 추론된 payload capability를 inspect해 이 검증을 중복하면 안 됩니다. governance boundary는 session intent, current work routing, completion evidence, strict audit, 문서화된 work contract입니다. malformed tool payload는 host schema validator의 책임으로 남깁니다.

## `_shared/install_hooks.py`

`check_status_detail(platform_key)`는 structured hook state를 반환합니다. 기존 `check_status(platform_key)`는 compatibility wrapper로 남아 `installed`, `missing`, `skipped`, `unsupported`만 반환합니다.

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

`HOOK_MISSING`는 `missing_reason`을 사용합니다.

| missing_reason | trigger |
| --- | --- |
| `config_file_absent` | hook config file이 없음 |
| `suite_incomplete` | hook config는 있지만 required marker 중 하나 이상이 없음 |

`HOOK_INSTALLED_DRIFT`는 `no hooks`가 아닙니다. Ghost-ALICE marker는 있지만 installed command가 current installer payload와 더 이상 맞지 않는다는 뜻입니다.

## `agent-security-scan/scripts/scan_agent_security_surface.py`

`scan()`은 resolved path 기준으로 dedupe된 finding의 JSON list를 반환합니다. `finding_count`는 compatibility field로 유지하되, new consumer는 `finding_count_semantics`도 함께 inspect해야 합니다.

| field | meaning | compatibility |
| --- | --- | --- |
| `finding_count` | resolved path dedupe 이후 unique findings | existing |
| `finding_count_semantics` | `finding_count`의 literal semantics | new |
| `occurrence_count` | mirrors를 포함한 physical occurrence count, computed when available | optional |

## Completion, Recording and Delivery

Core `completion-route.v1`은 `evidence_route`와 `publication_route` (`none | prepare | publish | blocked`)를 구분합니다. criterion별 `check_ids`는 영향받은 owner 작업이며 `required_check_ids`는 전체 owner plan을 보존합니다. aggregate priority는 요약이지 모든 검증을 반복하라는 명령이 아닙니다. AP는 실제 current-ready qualified selection을 사용하며 backlog나 run 존재만 사용하지 않습니다.

지원되는 업무 결과, prepared/published request, applied request와 전체 단위 완료는 별개입니다. unavailable proof는 pass가 아닙니다. 변경 없는 proof의 `proof_ref`와 `verified_at`은 새 입력 연결에서도 원본을 유지합니다. 첫 recording-only failure는 bounded repair를 허용하며 동일한 실패는 관련 변화까지 보류합니다. 기존 human/native permission과 독립적으로 승인된 작업은 유지됩니다.

Core `response_delivery.py`는 usable visible prior payload와 unchanged claims에만 `preserve_answer_and_patch_metadata`를 반환합니다. final-only/unknown/missing prior payload는 `assembled_complete_payload`, changed claims는 `corrected_complete_answer`를 사용합니다. selector는 domain truth나 native permission을 대신하지 않습니다. 근거 없는 주장은 철회하거나 partial로 보고합니다. JSON export, printed io-trace 또는 legacy classifier label은 runtime authority나 완료 근거가 아닙니다.
