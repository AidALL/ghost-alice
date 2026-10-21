# Session Gate Matrix

Language: [🇺🇸 English](../../policies/session-gate-matrix.md) | 🇰🇷 한국어

기계가 읽는 게이트 인덱스는 [skill-catalog/session-gates.json](../../../skill-catalog/session-gates.json)입니다. 이 문서는 각 조건의 절차 담당자를 연결합니다. [아키텍처](../../../architecture_ko.md)는 구성요소의 책임을, 연결된 스킬은 에이전트의 행동을 정의합니다.

## Contents

- [Required Entrypoints](#required-entrypoints)
- [Turn Routing Contract](#turn-routing-contract)
- [Session Intent Ledger Contract](#session-intent-ledger-contract)
- [Runtime Hook Graph Contract](#runtime-hook-graph-contract)
- [Dynamic Focus Contract](#dynamic-focus-contract)
- [Routing Surface Contract](#routing-surface-contract)
- [Work-Impact Projection Contract](#work-impact-projection-contract)
- [Runtime Checkpoints](#runtime-checkpoints)
- [tool-checkpoint Visible Surface](#tool-checkpoint-visible-surface)
- [tool-checkpoint Batch / Continuation Compression](#tool-checkpoint-batch--continuation-compression)
- [Notes](#notes)
- [Managed Completion Recording Contract](#managed-completion-recording-contract)
- [Current Selection and Bounded Recovery](#current-selection-and-bounded-recovery)
- [Repair and authority conditions](#repair-and-authority-conditions)


## Required Entrypoints

| 조건 | 현재 세션 어시스턴트가 따를 절차 |
| --- | --- |
| 모든 사용자 입력 | 네이티브 접수 관찰 후 해석. 새 해석이나 바뀐 해석이 있는 작업은 첫 도구 호출 전에 `interpreted-as: `를 기록하고, 판단이 저장된 의도에 기댈 때만 그 의도를 읽습니다 |
| 현재 세션 어시스턴트가 boundary-contract: required를 선택 | 의존 작업 전에 boundary-contract |
| 개발 작업 시작 | coding-convention/using-coding-convention |
| 버그 원인 조사 | coding-convention/systematic-debugging |
| 구현할 동작이 알려져 있고 검사 가능 | coding-convention/test-driven-development |
| 실행한 작업의 완료 또는 부분 성공 주장 | coding-convention/verification-before-completion |
| 커밋·푸시 또는 브랜치 통합 | coding-convention/finishing-a-development-branch |
| 새 작업·파일·검사 주기·후속 작업 정의 | necessity-gate |

## Turn Routing Contract

네이티브 사전 확인은 관찰로 남습니다. 어시스턴트는 그 결과를 재사용해 필요한 작업을 고르고, 영향받는 스킬을 바꾸기 전이나 사용자가 백업 검토를 명시적으로 요청했을 때 merge-companion을 적용합니다. 관련 없는 읽기 전용 조회는 그 절차를 부르거나 매니페스트를 다시 확인하지 않습니다. 수동 사전 확인이 빠졌으면 영향받는 스킬을 바꾸기 전에 합니다. 명시적으로 미루면 항목은 미결로 남습니다.

현재 세션 어시스턴트는 후속 질문이나 상태 질문도 현재 의도와 계속 유효한 권한으로 분류합니다. 이전 경로는 현재 입력의 경로가 아닙니다. 현재 세션 어시스턴트는 필요할 때 같은 정확한 세션에 skill-evolution을 별도로 적용하여 보고합니다. 이 절차는 task-router에 입력을 전달하거나 작업을 배정하지 않습니다.

현재 세션 어시스턴트는 [task-router](../../../task-router/SKILL.md)를 적용하여 작업을 선택하고 현재 경로 후보를 작성합니다. [boundary-contract](../../../boundary-contract/SKILL.md)를 적용하여 허용 대상·제외 항목·자원을 선언합니다. 이 절차들은 네이티브 권한을 부여하지 않습니다.

## Session Intent Ledger Contract

Core는 원문을 저장하지 않고 확정된 사용자 입력의 다이제스트·문자 수·순서가 있는 이벤트를 기록합니다. Codex는 접수 시 바로 기록합니다. Claude UserPromptSubmit은 다이제스트·문자 수·네이티브 연결 정보를 임시 기록하며, 신뢰된 PreToolUse 또는 Stop 래퍼가 호스트의 대화 기록 추가 후 발신 정보를 대조합니다. 사용자 입력은 작업 중에 친 입력을 포함해 기준 입력을 갱신합니다. SDK가 시작한 턴과 작업 중에 앱이 넣은 이벤트는 권한 없는 비인간 문맥이 됩니다. 작업 알림과 하위 에이전트 회신은 마지막 사용자 입력과 경로를 유지합니다. 출처를 판정하지 못했거나 대화 기록에서 찾지 못한 관찰은 뒤에 찾은 사용자 입력이 넘깁니다. 그런 입력이 없으면 그 관찰은 pending으로 남고 의존 쓰기는 거부됩니다. PreToolUse 또는 Stop 훅 래퍼가 그 대기를 기록하고, 같은 세션·같은 원인으로 사용자에게 한 번 경고합니다. 판정이 실패하면 래퍼가 그 실패를 기록하고 안쪽 훅은 그대로 실행합니다. 현재 세션 어시스턴트는 목표·제약·결정·질문·기준이 실질적으로 바뀔 때만 압축된 의미 변경을 기록합니다.

실행 소비자는 세션 영수증의 정확한 root/platform/session과 원장의 현재 입력으로 공개 API를 호출합니다. SQLite가 권위 있는 저장소입니다. intent-state.json과 intent-events.jsonl은 가져오기·내보내기 형식이며 current-session.json은 탐색 힌트입니다. 어느 것도 쓰기 식별자를 대신하지 않습니다. 기록이 없으면 의존 작업은 미해결로 남습니다. 어시스턴트는 목적이나 필수 의존 관계에 필요하며 기존 권한 안에 있는 복구만 선택하고, 식별자나 권한을 추측하지 않습니다.

[Session-intent-analyzer](../../../session-intent-analyzer/SKILL.md)는 접수와 정정 출처를 담당합니다. [원장 스키마](../../../session-intent-analyzer/references/ledger-schema.md)는 상태 필드·트랜잭션·이관 해시·현재 응답 경로를 정의합니다. 접수 이벤트와 intent-state 필드에는 원문 프롬프트·전체 대화·도구 출력·시스템/개발자 지침·비밀 값을 저장하지 않습니다. 별도의 Core 검토 테이블(`core_completion_candidates`, `core_completion_assessments`, `core_completion_responses`)은 승인된 답변·원본 출처 스냅샷과 모델의 검토 판단을 보존합니다. 이 검토 기록은 세션 의도 요약과 구별됩니다. 훅 없는 작업도 같은 식별 계약을 따르며, 바로 답할 때는 도구를 쓰지 않습니다.

## Runtime Hook Graph Contract

1. session-intent-analyzer SessionStart 훅이 세션 영수증을 제공합니다. UserPromptSubmit 훅은 입력마다 입력을 기록하고, 그 영수증을 보내지 못했거나 접수에 실패했을 때만 모델 문맥을 더합니다. Core는 Codex의 직접 접수 또는 Claude의 신뢰된 대조 절차로 정확한 기준 사용자 입력을 확정합니다. Read-state는 읽기만 수행합니다. 현재 세션 어시스턴트는 task-router를 적용하여 작업을 선택하며, 보고가 필요하면 같은 접수에 skill-evolution을 별도로 적용합니다.
2. 현재 세션 어시스턴트는 정상 경로 후보를 Core에 제출합니다. Core Python 코드는 현재 입력·소유권·승인·선택 기준을 확인한 뒤 ready 경로를 기록합니다.
3. 현재 세션 어시스턴트는 필요한 경계와 담당 스킬을 따릅니다. 호스트는 도구 권한을 적용하고 허용된 호출을 실행합니다. 해당 Ghost-ALICE Autopilot 사전 훅은 출처를 기록하고 PostToolUse는 엄격한 감사를 유지합니다.
4. 완료 시 가드와 Ghost-ALICE Autopilot 어댑터는 같은 입력·본문에 연결된 Core 평가를 호출합니다. 독립적으로 등록된 훅 사이의 정책 실행 순서를 가정하지 않습니다.

Tool-checkpoint는 도구 호출 전 단계(호스트의 `PreToolUse`/`BeforeTool`)에서 실행되며 사용자 입력 접수가 아닙니다. 도구 입력이나 감사 메타데이터는 네이티브 권한을 부여하지 않습니다. 설치 설정만으로 훅 호출이나 호스트 실행을 입증할 수 없으며, 필요한 실행 관찰은 [live-smoke-regression](live-smoke-regression.md)이 담당합니다.

## Dynamic Focus Contract

현재 세션 어시스턴트는 판단 대상을 선택합니다. micro는 개별 결과나 필드, meso는 하위 작업이나 출처와 대상의 대응 관계, macro는 통합 결과와 문서 간 일관성, meta는 필요성·작업 정의·전제를 다룹니다. 불일치가 관찰된 위치가 반드시 원인은 아닙니다. 조사가 필요하면 근거에 따라 범위를 넓히거나 좁히거나 같은 범위의 다른 대상으로 옮길 수 있습니다. 판단 범위는 검증 부담과 독립적이며, 이 계층은 차례로 올라가는 절차도 권한 확장도 아닙니다. meta 검토 자체가 사용자 질문을 요구하지는 않습니다.

작업 선택이 달라져야 할 때 현재 세션 어시스턴트는 결과와 반대 근거를 입력으로 task-router를 다시 적용합니다. 결과를 관련 대조 기준과 비교하고 필요한 승인 수정을 선택할 만큼 원인을 조사합니다. 목적과 필수 의존 관계에 영향이 없으면 해당 사항을 미해결로 남기거나 비필수 작업을 제외할 수 있으며, 불일치가 있다는 이유만으로 복구 작업을 만들지 않습니다. 전체 결과를 포함한 필수 조건에는 여전히 뒷받침하는 근거가 필요합니다. 필수 검사는 유지합니다. 재량 검사는 가능한 결과가 다음 판단을 바꿀 때만 실행하며, 중단 조건은 아래 근거 선택 담당 절차가 정의합니다. 정적인 calls 그래프가 반복 검사를 지시하지는 않습니다.

## Routing Surface Contract

현재 세션 어시스턴트는 session-intent-analyzer를 적용하여 의미 사실과 누적 결정을 Python 원장 기록기에 제출합니다. 현재 대화의 응답 모델은 task-router를 적용하여 작업을 판단합니다. 표시 코드와 어시스턴트는 routing-surface의 기존 change-depth·focus-layer·verification-complexity 값을 사용하고 별도 척도를 만들지 않습니다. 알 수 없거나 모순되는 값은 더 충분히 표시하고 영향받은 선택을 다시 판단합니다.

task-router를 따르는 현재 세션 어시스턴트는 주 요청과 최종 목적을 보존합니다. 조사나 worktree 확인은 사용자가 산출물로 요구하지 않았다면 수단입니다. Accepted-continuation에는 기록된 수락이 필요하며 근거 요구를 낮추지 않습니다.

[경로 세부](../../../task-router/references/routing-details.md)는 바로 답하기(`direct-response`)와 확인 질문(`clarification-only`)을 작업과 구별합니다. 일반적인 이유·방법 질문의 전제나 열린 작업 공간만으로 로컬 진단을 승인하지 않습니다. 상태 질문이나 정정은 보통 미완료 승인 작업을 조정하며, 명시적 중단·일시정지는 계속 여부를 바꿉니다. 바로 답하기는 제공된 대화를 사용하고 에이전트 측 도구나 메타데이터 복구 없이 답변만 반환합니다. 확인 질문은 공통 계약의 Session Intent 절이 정한 형식을 따릅니다. 이 조건은 작업 완료 경로의 승인된 경로 등록을 금지하지 않습니다.

## Work-Impact Projection Contract

훅은 다음 작업의 경계·초점·검증 부담·복구를 바꾸는지에 따라 값을 분류합니다.

- agent_visibility.profile은 표시량을 조정하며 훅 실행이나 엄격한 감사를 끄지 않습니다.
- 강제·위험·게이트 값과 검증 실패는 전체 모델 안내와 강제 표시를 유지합니다.
- 일상·디버그 값은 엄격한 로그에 남고 작업 판단에 영향을 줄 때만 모델 안내에 포함됩니다.
- 알 수 없거나 모호하거나 실패한 값은 더 충분한 맥락을 표시하고 해당 판단을 다시 검토합니다.

중복된 정상 안내·변화 없는 감사 항목·상관관계 메타데이터만으로 새 작업을 만들지 않습니다. 토큰 감소는 결과이며 성공 지표가 아닙니다.

## Runtime Checkpoints

결정에 영향을 주는 경로 안내는 [task-router](../../../task-router/SKILL.md#referenced-contracts)가 담당합니다. 현재 세션 어시스턴트는 새로 읽거나 유효하게 보관한 지침 본문을 사용하고 그 절차를 따른 뒤에만 스킬 호출을 기록합니다. 메타데이터 일치는 실행이 아닙니다.

[Verification-before-completion](../../../coding-convention/verification-before-completion/SKILL.md#completion-check-format)은 공통 completion-check·partial-check 형식과 기준·주장·근거 대응을 담당합니다. Core는 현재 소유 경로에 연결된 주장·원본 자료·응답 전체의 검토를 인증하여 완료 제출을 수락합니다. 스킬 로드는 절차 안내와 감사 활동이며 별도의 완료 선행 조건이 아닙니다. 최종 완료에는 미검증 기준 없이 근거가 지원하는 판정이 필요합니다. 부분 성공은 전체 계약과 미해결 기준을 유지하며 성공한 일부를 전체 완료로 제시하지 않습니다.

현재 세션 어시스턴트는 변하지 않은 유효한 근거의 원본 출처와 시각을 재사용합니다. 형식이나 경로 오류만으로 업무를 다시 검증하지 않습니다. 화면에 보인 기록은 증거가 되지 않으며, 엄격한 내부 감사와 실제 절차 근거는 유지합니다. 제어 필드와 열거 값은 두 언어 문서 모두 영어를 유지합니다.

## tool-checkpoint Visible Surface

공통 계약의 io-trace Transparency Rule이 체크포인트 표시를 맡습니다. 현재 세션 어시스턴트는 도구 호출의 결과가 권한, 다음 판단 또는 필요한 검증을 바꿀 때 체크포인트를 표시합니다. 바뀌지 않은 증거는 다시 쓰고, 판단에 영향을 줄 수 있는 자료만 읽습니다. 영수증이나 정상적인 도구 호출만으로는 메시지를 다시 표시하지 않습니다.


## tool-checkpoint Batch / Continuation Compression

훅은 모든 호출을 검사하고 엄격한 감사를 보존하며 같은 입력에서 변하지 않은 사용자 표시만 생략합니다. Batch와 continuation 형식은 권한을 부여하지 않습니다.

Continuation은 같은 process/session/tool-call ID를 다시 조회하는 경우만 뜻합니다. 거부, 불일치, 관련 상태 변경이 있으면 새 체크포인트가 필요할 수 있습니다. 조회나 새 입력만으로는 안내를 다시 표시하지 않습니다.

## Notes

절차 담당자를 지정하면 중복 규칙의 불일치를 줄일 수 있습니다. 현재 세션 어시스턴트는 [지침 본문 재사용](../../../coding-convention/verification-before-completion/references/verify-or-reuse.md)을 따르고 실제 읽기 또는 유효한 재사용을 내부에 기록합니다. 일상적인 정상 상태는 반복하지 않으며 미결·변경·실패 상태는 판단에 필요한 안내를 유지합니다.

소스 수정, 지정 검사 통과, 설치된 바이트, 네이티브 실행, 수락된 접수, 적용된 기준과 보이는 답변은 각각 다른 주장입니다. 한 단계의 근거가 다른 단계를 입증하지는 않습니다.

## Managed Completion Recording Contract

새 완료 또는 부분 완료 제출에서 Core Python 코드는 증거 형식과 인증된 의미 검토보다 현재 소유 경로를 먼저 확인합니다. 따라서 경로가 없으면 증거 필드 수정을 요구하기 전에 경로 오류를 반환합니다.

이 조건에서는 응답 모델이 [담당 검토 절차](../../../coding-convention/verification-before-completion/references/autopilot-publication.md#prepare-and-record-the-response-review)에 따라 두 판단을 기록합니다. [Core 응답 흐름](../../../architecture_ko.md#근거와-네이티브-표시)은 인증과 네이티브 답변 전달을 설명하며, [출력 의미](tool-output-semantics.md#검토출력계속-작업)는 지원·미지원 결과를 구분합니다.

등록된 관리 훅은 v2의 작업 단위별 승인 기준을 사용하고 직접 공개 API는 v1 기본값을 유지합니다. 소스 API·실행 모듈·원장·프로젝트·설치 스킬·플랫폼 상태는 별도의 위치 연결을 유지합니다. 일반 업데이트는 원장 권위를 보존하며 전송 방식이 origin·세션·접수·승인을 부여하지 않습니다.

관리형 네이티브 Stop에서는 Ghost-ALICE Autopilot 어댑터가 준비된 출처 영수증과 Core가 인증한 검토로 해당 근거를 기록합니다. 모델이 이 경로를 위해 별도 게시 명령을 실행하지 않습니다. 수락된 접수는 pending-adapter이며 적용된 완료가 아닙니다. Ghost-ALICE Autopilot 어댑터는 기록을 인증하고 Core에 보호된 기준 갱신을 요청합니다. 근거가 없거나 미해결인 기준은 unmet으로 남습니다. 근거가 있는 보고도 작업 기준을 충족하지 않을 수 있으며 이때 제출이 필요하지 않을 수 있습니다.

## Current Selection and Bounded Recovery

Core는 task-router를 따르는 모델이 제출한 현재 응답 경로를 검증합니다. 바로 답하기나 확인 질문의 투영은 실행 단위를 선택하지 않으며, 요청된 조회는 작업입니다. 새 입력은 경로 연결을 바꾸되 안정된 작업 단위와 변경 없는 원본 근거를 보존합니다.

구체적인 경로·증거·기록 오류는 아래의 담당 절차로 돌아갑니다. 모델은 목적이나 필수 의존 관계에 필요하고 권한 안에 있을 때만 수정을 선택합니다. 지원되지 않는 주장은 수정·철회하며 필수 미해결 기준은 미충족 상태로 남습니다. 동일한 기록 실패가 반복되면 의존 적용을 보류하되 업무를 재실행하거나 독립적인 승인 작업을 막지 않습니다.

[검토·전달 절차](../../../coding-convention/verification-before-completion/references/autopilot-publication.md)가 호출 순서와 정정 답변의 형태를 담당합니다. [출력 의미](tool-output-semantics.md#검토출력계속-작업)는 반환 선택 값을 정의합니다. Core의 과거 결과 보고 경로는 원본 적용 근거가 정확한 문장 전체를 뒷받침할 때만 적용하며, 추가 문구나 더 넓은 주장에는 별도 검토가 필요합니다.

## Repair and authority conditions

- [Boundary-contract: Authority and renewal](../../../boundary-contract/SKILL.md#authority-and-renewal)은 사용자 권한·허용 대상·자원 제한을 담당합니다.
- [Systematic-debugging: Incident accounting and continuation](../../../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation)은 실패한 수정 회차·재평가·한정된 계속 진행을 담당합니다.
- [Verification-before-completion: Evidence Selection And Stop Gate](../../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate)은 유용한 검사와 근거 재사용을 담당합니다.
- [Ghost-ALICE Autopilot publication contract](../../../coding-convention/verification-before-completion/references/autopilot-publication.md)는 캡처·접수·적용·기록 복구를 담당합니다.

담당 절차는 누적 시도·원본 출처와 시각·영향 없는 기준을 보존합니다. 한 경로의 실패는 그 의존 작업만 막으며 다른 승인 작업을 막지 않습니다.
