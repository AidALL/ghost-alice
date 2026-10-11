# Tool Output Semantics

언어: [🇺🇸 English](../../policies/tool-output-semantics.md) | 🇰🇷 한국어

출력 담당자는 이 계약에 따라 사람이 읽는 표시 이름과 기계가 읽는 상태 필드를 구분합니다. 이 계약은 넓은 새 상태 분류를 만들도록 허가하지 않습니다. 실제로 관측된 오진과 직접 연결되고 사용자의 행동을 바꾸는 출력만 추가합니다.

## Contract

- 상태를 읽는 구성 요소는 기계 판독 필드를 제어 값으로 사용합니다.
- 사람이 읽는 이름은 표시용입니다.
- 호환성 필드를 실제 기계 상태로 해석하지 않습니다.
- 출력 담당자는 사용자 행동이 달라질 때만 새 필드를 추가합니다.

## Host Tool Input Schema Validation

도구 실행 호스트는 도구 인수 검증을 담당합니다. Ghost-ALICE 훅은 호스트의 도구 스키마를 중복 검증하지 않습니다. 호스트가 인수 검증 실패를 보고하면 실제 오류에 따라 제출 인수를 수정한 뒤 승인된 행동을 재시도합니다.

Ghost-ALICE 훅은 직렬화된 임의 `tool_input`, 금지 문자열이나 추정한 기능을 검사하여 호스트의 인수 검증을 중복하지 않습니다. 거버넌스는 세션 의도, 현재 작업 라우팅, 완료 근거, 엄격한 감사와 문서화된 작업 계약을 담당합니다. 잘못된 도구 인수의 검증은 호스트가 담당합니다.

## `_shared/install_hooks.py`

`check_status_detail(platform_key)`는 structured hook state를 반환합니다. `check_status(platform_key)`는 `installed`, `missing`, `skipped`, `unsupported`를 반환하는 compatibility wrapper입니다.

| field | meaning | role |
| --- | --- | --- |
| `status_token` | 기계가 읽는 훅 상태 | 구조화된 상태 |
| `status_label` | 사용자 언어로 표시하는 이름 | 표시용 |
| `legacy_status` | 설치기 상태 문자열 | 호환성용 |
| `details` | 누락·불일치 훅 이름과 이유 메타데이터 | 구조화된 상태 |
| `missing_reason` | 원인을 알 때 기록하는 `HOOK_MISSING`의 이유 | 구조화된 상태 |
| `unsupported` | 플랫폼 실행 환경이 훅을 실행할 수 없을 때 true | 구조화된 상태 |

Hook status tokens:

| status_token | trigger | user action |
| --- | --- | --- |
| `HOOK_INSTALLED_OK` | 필수 표시와 예상 명령이 모두 일치 | 추가 조치 없음 |
| `HOOK_INSTALLED_DRIFT` | 필수 표시는 있지만 예상 명령이 다름 | 훅을 다시 설치하거나 로컬 훅 수정을 확인 |
| `HOOK_CONFIG_DISABLED` | 훅 설정은 있지만 실행 기능 옵션·설정이 훅을 비활성화 | 훅 설정 활성화 |
| `HOOK_MISSING` | 훅 설정 파일 또는 필수 표시가 없음 | 훅 설치 또는 누락된 표시 복원 |
| `HOOK_UNSUPPORTED` | 플랫폼 실행 환경이 훅을 실행할 수 없음 | 훅 없는 대체 절차 사용 |
| `HOOK_PLATFORM_MISSING` | 플랫폼 설정 디렉터리가 없음 | 해당 플랫폼 제외 또는 설치 |

`HOOK_MISSING`는 `missing_reason`을 사용합니다.

| missing_reason | trigger |
| --- | --- |
| `config_file_absent` | 훅 설정 파일이 없음 |
| `suite_incomplete` | 훅 설정은 있지만 필수 표시가 하나 이상 누락 |

`HOOK_INSTALLED_DRIFT`는 `no hooks`가 아닙니다. Ghost-ALICE 표시는 있지만 설치된 명령이 현재 설치기가 생성하는 명령과 맞지 않는다는 뜻입니다.

## `agent-security-scan/scripts/scan_agent_security_surface.py`

`scan()`은 resolved path 기준으로 dedupe된 finding의 JSON list를 반환합니다. `finding_count`를 사용하는 consumer는 `finding_count_semantics`도 함께 확인합니다.

| field | meaning | role |
| --- | --- | --- |
| `finding_count` | resolved path dedupe 이후 unique findings | count |
| `finding_count_semantics` | `finding_count`의 literal semantics | count interpretation |
| `occurrence_count` | mirrors를 포함한 physical occurrence count, computed when available | optional |

## Completion, Recording and Delivery

응답 모델은 각 주장과 원본 출처를 먼저 비교하고, 답변 전체와 사용자 목적·제약을 따로 판단합니다. Core는 기록된 두 판단을 인증합니다. [아키텍처](../../../architecture_ko.md)는 모델·호스트·코드의 전달을, [검토·접수 절차](../../../coding-convention/verification-before-completion/references/autopilot-publication.md)는 실제 호출과 기록 복구를 담당합니다.

| 출력 | 생성자와 소비자 | 의미 |
| --- | --- | --- |
| `status=reviewed`, `response_text` | Core 검토 API → 호스트 도구 결과 → 응답 모델 | 필요한 판단이 정확한 답변을 뒷받침하므로 모델이 제출할 수 있습니다. 미완료 작업을 정확히 보고하는 답변일 수도 있습니다. |
| `status=unsupported` 또는 구체적인 선행 조건 오류 | Core 검토 API → 호스트 도구 결과 → 응답 모델 | 제출할 수 있는 지원 답변을 반환하지 않습니다. 모델은 담당 절차에 따라 영향받은 내용을 수정하거나 미해결 상태를 보고합니다. |
| `evidence_route`, `publication_route` | Core `completion-route.v1` → 네이티브 완료 처리기 | 근거 선택과 접수 상태(`none | prepare | publish | blocked`)는 별개입니다. |
| `check_ids`, `required_check_ids` | Core 완료 경로 → 영향받은 작업 소비자 | 앞 필드는 영향받은 담당 검사를, 뒤 필드는 담당자의 전체 계획을 유지합니다. 전체 우선순위는 모든 검사의 반복 지시가 아닙니다. |
| `proof_ref`, `verified_at` | 원래 검토 근거 → Ghost-ALICE Autopilot 기록·적용 | 변경 없는 재사용에서도 원본 참조와 검증 시각을 유지합니다. |
| `pending-adapter`, 적용 결과 | Ghost-ALICE Autopilot 어댑터 → 네이티브 호스트와 적용 기록 소비자 | 기록 접수와 보호된 기준 적용은 별개입니다. 없거나 뒷받침되지 않는 근거로 기준을 충족시키지 않습니다. |
| Stop 상태와 선택적 작업 요청 | 독립 호출된 Core 가드 / Ghost-ALICE Autopilot 어댑터 → 호스트 | 가드는 허용 또는 구체적 거부를 반환합니다. Ghost-ALICE Autopilot은 수락된 계획에서만 작업을 선택합니다. 요청 반환은 호스트 전달이나 업무 실행의 근거가 아닙니다. |

Core `response_delivery.py`는 제공된 호스트 정보로 답변 구성을 선택합니다.

| 선택 값 | 조건 |
| --- | --- |
| `preserve_answer_and_patch_metadata` | 사용 가능한 이전 답변이 보이며 주장이 같습니다. |
| `assembled_complete_payload` | 최종 응답만 표시하거나 사용 가능한 이전 답변이 없습니다. |
| `resolve_host_delivery` | 모델이 제공된 대화·호스트 정보로 알 수 없는 표시 여부를 판단해야 합니다. |
| `corrected_complete_answer` | 답변의 주장이 바뀌었습니다. |

이 선택 값은 내용의 진실성이나 권한을 입증하지 않습니다. [Apply and deliver 절차](../../../coding-convention/verification-before-completion/references/autopilot-publication.md#apply-and-deliver)는 유효한 근거·이미 보이는 동일 본문의 보존을 포함하여 모델의 다음 행동을 정의합니다. [세션 게이트 담당 표](session-gate-matrix.md#repair-and-authority-conditions)는 권한·복구·유용한 검사의 담당 절차를 연결합니다.
