# Tool Output Semantics

언어: [🇺🇸 English](../../policies/tool-output-semantics.md) | 🇰🇷 한국어

이 문서는 human-readable label과 machine-readable field를 분리하는 minimum contract입니다. 넓은 status taxonomy를 새로 만들라는 허가가 아닙니다. 실제로 관찰된 misdiagnosis와 직접 이어지는 output surface만 추가합니다.

## Contract

- machine-readable field가 control surface입니다.
- human label은 표시용일 뿐입니다.
- compatibility field를 machine state로 해석하면 안 됩니다.
- user action이 달라질 때만 new field를 추가합니다.

## Host Tool Input Schema Validation

tool argument의 형태는 Ghost-ALICE governance hook이 아니라 host harness가 소유합니다. tool call이 required field를 빠뜨리면, host는 tool body가 시작되기 전에 submitted argument를 해당 tool JSON Schema에 대해 validate합니다. `questions` 같은 required array가 없는 AskUserQuestion-style call은 실행 전 validation failure입니다. user UI는 열리지 않고 file이나 external side effect도 생길 수 없습니다. 복구는 같은 intent를 schema에 맞는 argument로 다시 호출하는 것입니다.

Ghost-ALICE hook은 arbitrary serialized `tool_input` 내용, deny-list substring, 추론된 payload capability를 inspect해 이 검증을 중복하면 안 됩니다. governance boundary는 session intent, current work routing, completion evidence, strict audit, 문서화된 work contract입니다. malformed tool payload는 host schema validator의 책임으로 남깁니다.

## `_shared/install_hooks.py`

`check_status_detail(platform_key)`는 structured hook state를 반환합니다. `check_status(platform_key)`는 `installed`, `missing`, `skipped`, `unsupported`를 반환하는 compatibility wrapper입니다.

| field | meaning | role |
| --- | --- | --- |
| `status_token` | machine-readable hook state | structured state |
| `status_label` | localized human label | presentation only |
| `legacy_status` | installer status string | compatibility only |
| `details` | missing or drifted hook labels and reason metadata | structured state |
| `missing_reason` | `HOOK_MISSING`의 reason when known | structured state |
| `unsupported` | platform runtime이 hooks를 run할 수 없을 때 true | structured state |

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

`scan()`은 resolved path 기준으로 dedupe된 finding의 JSON list를 반환합니다. `finding_count`를 사용하는 consumer는 `finding_count_semantics`도 함께 확인합니다.

| field | meaning | role |
| --- | --- | --- |
| `finding_count` | resolved path dedupe 이후 unique findings | count |
| `finding_count_semantics` | `finding_count`의 literal semantics | count interpretation |
| `occurrence_count` | mirrors를 포함한 physical occurrence count, computed when available | optional |

## Completion, Recording and Delivery

Core `completion-route.v1`은 `evidence_route`와 `publication_route` (`none | prepare | publish | blocked`)를 구분합니다. criterion별 `check_ids`는 영향받은 owner 작업이며 `required_check_ids`는 전체 owner plan을 보존합니다. aggregate priority는 요약이지 모든 검증을 반복하라는 명령이 아닙니다. AP는 실제 current-ready qualified selection을 사용하며 backlog나 run 존재만 사용하지 않습니다.

지원되는 업무 결과, prepared/published request, applied request와 전체 단위 완료는 별개입니다. unavailable proof는 pass가 아닙니다. 변경 없는 proof의 `proof_ref`와 `verified_at`은 새 입력 연결에서도 원본을 유지합니다. 첫 recording-only failure는 bounded repair를 허용하며 동일한 실패는 관련 변화까지 보류합니다. 기존 human/native permission과 독립적으로 승인된 작업은 유지됩니다.

Core `response_delivery.py`는 usable visible prior payload와 unchanged claims에만 `preserve_answer_and_patch_metadata`를 반환합니다. final-only/unknown/missing prior payload는 `assembled_complete_payload`, changed claims는 `corrected_complete_answer`를 사용합니다. selector는 domain truth나 native permission을 대신하지 않습니다. 근거 없는 주장은 철회하거나 partial로 보고합니다. JSON export 또는 printed io-trace는 runtime authority나 완료 근거가 아닙니다.

## 복구와 검사 판단

현재 입력과 이전의 실제 사용자 지시가 목표·금지 사항·허용 행동과 대상·부수 효과·자원에 관한 권한의 출처입니다. 보관된 권한 사실과 신뢰된 system/project 제약에는 각각 출처를 유지합니다. intake와 router가 권한을 부여하지는 않습니다. 사용자가 정한 기준과 자원 한도는 에이전트가 도출한 해석이나 한정된 실행 계획과 구별합니다. 명시적 자원 한도가 없다는 이유로 승인된 한도를 만들지 않습니다. 실제 권한 안에서 구현 계획을 갱신하되 수락 기준을 약화하지 않습니다. 경계를 넘기 전에 정확한 사람 소유 결정을 요청합니다. 실제 승인만 해당 계획과 권한을 갱신하며, 거부는 해당 경로를 보류하고 응답이 없으면 대기를 유지합니다.

실패한 복구 라운드는 수정과 완료된 해당 관측에서 같은 미해결 실패가 확인된 경우입니다. 최초 실패는 0회입니다. 미해결 실패·기준·단계·대상으로 사건을 구별하고 각 결과가 원인·매핑·설계·검증 판단에 무엇을 바꾸는지 설명합니다. 담당자나 세션이 바뀌어도 누적 시도와 최초 다섯 회 재평가의 처리 여부를 유지합니다. 원인을 모른다는 이유로 이름이나 횟수를 초기화하지 않으며, 같은 오류 문구만으로 서로 다른 업무·기록 실패를 합치지 않습니다. 해결이 입증된 뒤 재발하면 연결된 새 사건으로 다룹니다. 다섯 회는 최초 필수 전제 재평가 전의 최대 실패 횟수이며 최소 재시도 횟수나 전체 작업 한도가 아닙니다. 이 재평가를 처리했다면 누적 여섯·일곱 회라는 사실만으로 다시 발동하지 않습니다. 새로운 관련 반대 근거나 현재 중단 조건은 별도로 재평가를 요구할 수 있습니다. 계속하려면 구별 가능한 가설·검사·명시적인 추가 시도 또는 자원 한도·중단 조건이 남은 전체 자원 안에 있어야 합니다. 재평가는 횟수나 자원을 초기화하지 않으며 한도가 소진되면 해당 진행을 보류합니다. 다섯 회만으로 사람에게 결정을 요구하지 않습니다.

재량 검사는 미해결 기준이나 경쟁 설명, 검사가 이를 구별하는 이유, 서로 다른 구체적 다음 행동·수락 판단·경계 결정으로 이어지는 결과를 명시합니다. 무언가 발견할 가능성만으로는 부족합니다. 확률적 반복에는 불확실성·표본 추출·중단 규칙이 필요합니다. 필수 검사는 유지하며 필수 근거가 없거나 기준이 미해결이면 완료할 수 없습니다. 변하지 않은 유효한 proof는 원래 출처와 시간으로 재사용합니다. 이미 실행한 업무의 상태가 불명확하면 해당 관측을 복구하며 업무를 자동 재실행하지 않습니다. 아직 실행하지 않은 필수 업무는 실제 실행이 필요합니다. 불확정 검사는 업무 성공·실패나 복구 실패 횟수를 확정하지 않지만 관측·기록 시도도 한정된 자원을 소비합니다.

기존 작업 문맥과 인계에 판단을 유지하며 별도 counter hook·classifier·매 대화 기록·필수 보고서를 만들지 않습니다. 변하지 않은 동일 기록 실패는 담당 typed recovery에 따라 다섯 회를 기다리지 않고 보류합니다. 기록만 실패했다면 업무 proof를 유지합니다. 보류·사람 응답 대기 경로는 실제 재개 조건 전까지 선택에서 제외합니다. 선택할 업무가 없으면 모든 필수 기준의 유효한 proof와 관련 반대 근거를 확인하여 담당 완료 검증으로 진행하며, 충족하지 못하면 정직한 부분·대기 상태를 반환합니다.

담당 workflow는 선택한 업무나 수정을 호출하고 실제 결과를 받으며, 선언한 실행 결과 자체가 검증 관측이 될 수 있습니다. 도메인 검증은 실제 관측·출처·시간을 workflow에 반환하여 기준을 판단하게 합니다. 적용되는 등록 v2 pretool은 도구 실행 전에 AP origin·receipt를 자동 준비하고 현재 qualified 명령 문맥을 반환합니다. 유효한 준비 결과를 재사용하며 적용되는 의존성이 실제 미충족일 때만 반환된 명시적 준비 helper를 사용합니다. 기록 누락은 capture·publication·application의 기록 가능성에 영향을 주며 AP pretool은 계속 진행하고 native 도구 권한은 host가 담당합니다. 필수 또는 판단을 구별하는 업무 관측은 자체 권한·자원 한도를 따르며 기록 복구 규칙은 기록 시도에만 적용됩니다. 기록이 불가능한 경우에도 유효한 proof와 원래 출처·시간을 유지합니다.

후보 완료를 위해 담당 workflow가 근거를 얻거나 유효한 proof를 재사용합니다. 적용되는 AP publication은 정확한 현재 qualified 선택·prepared AP receipt·실제 반환된 publish 명령으로 pending-adapter 요청을 만듭니다. Native candidate/final dispatch는 실제 등록된 handler를 호출합니다. 완료 guard는 후보의 필드와 해당 skill 근거를 확인하고 privileged AP Stop adapter는 publication·proof·소유권·의존성을 재검증합니다. 현재 유효한 publication만 공유 권한 transaction에서 정확한 Core 기준 writer로 진행합니다. 누락·오래됨·무효 상태는 기준 기록 없이 기록 진단을 반환합니다. 실제 application 상태와 남은 기준이 단위별 기록·완료 주장의 근거입니다. Handler는 실제 native 호출 규칙을 따릅니다. Core는 전달 정책과 허용된 payload 조합을 담당하며 workflow/native host가 실제 화면에 전달합니다. 후보 주장은 현재 지원되는 범위를 사용하고 실제 기록·적용·관측된 전달 결과가 후속 복구와 완료 주장을 결정합니다. 이미 보이는 변경 없는 업무 본문은 해당 control-only repair로 보존합니다. 가능한 후속 조치는 workflow로 반환하고 선택 가능한 진행이 없으면 정직한 부분·대기 상태를 반환합니다.
