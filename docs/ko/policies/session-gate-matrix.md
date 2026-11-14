# 세션 게이트 매트릭스

[🇺🇸 English](../../policies/session-gate-matrix.md) | [🇰🇷 한국어](./session-gate-matrix.md)

이 문서는 각 조건에 담당 절차를 연결합니다. [아키텍처](../../../architecture_ko.md)는 구성요소의 책임을, 연결된 스킬은 응답 모델의 행동을 정의합니다.

## 목차

- [필수 진입점](#필수-진입점)
- [턴 라우팅 계약](#턴-라우팅-계약)
- [세션 의도 원장 계약](#세션-의도-원장-계약)
- [런타임 훅 그래프 계약](#런타임-훅-그래프-계약)
- [동적 판단 범위 계약](#동적-판단-범위-계약)
- [routing-surface 계약](#routing-surface-계약)
- [Work-Impact Projection 계약](#work-impact-projection-계약)
- [런타임 체크포인트](#런타임-체크포인트)
- [tool-checkpoint 표시](#tool-checkpoint-표시)
- [참고](#참고)
- [완료 기록 계약](#완료-기록-계약)
- [현재 선택과 한정된 복구](#현재-선택과-한정된-복구)
- [수정과 권한 조건](#수정과-권한-조건)


## 필수 진입점

| 조건 | 응답 모델이 따를 절차 |
| --- | --- |
| 호스트가 `user` 역할로 전달한 모든 프롬프트 | 해석은 [공용 계약: Session Intent](../../../platforms/common-governance.md#session-intent), 기록은 [session-intent-analyzer](../../../session-intent-analyzer/SKILL.md) |
| 응답 모델이 boundary-contract: required를 선택 | 의존 작업 전에 boundary-contract |
| 개발 작업 시작 | coding-convention/using-coding-convention |
| 버그 원인 조사 | coding-convention/systematic-debugging |
| 인간 사용자의 요구 사항이나 원본 계약이 정한 프로덕션 동작을 시험이 실행할 수 있음 | coding-convention/test-driven-development |
| 실행한 작업의 완료 또는 부분 성공 주장 | coding-convention/verification-before-completion |
| 커밋·푸시 또는 브랜치 통합 | coding-convention/finishing-a-development-branch |
| 응답 모델이 새 작업·파일·감사 주기·후속 작업을 제안하거나 인간 사용자가 그 작업을 할지 물음 | necessity-gate |

## 턴 라우팅 계약

응답 모델이 병합 대기 매니페스트를 확인하고 그 항목을 검토하는 시점은 [merge-companion](../../../merge-companion/SKILL.md#entry-and-activation)이 정합니다.

인간 사용자가 skill-evolution 보고를 요청하거나 그 분석을 허가하면 응답 모델은 같은 세션에 skill-evolution을 별도로 적용할 수 있습니다. 이 절차는 task-router에 입력을 전달하지 않고 작업을 배정하지 않습니다.

응답 모델은 [task-router](../../../task-router/SKILL.md)를 적용하여 작업을 선택하고 작업 선택 기록 후보를 작성하며, [boundary-contract](../../../boundary-contract/SKILL.md)를 따라 인간 사용자의 지시가 허가한 작업 안에서 범위를 선언합니다. 이 절차들은 호스트의 실행 허용을 부여하지 않습니다.

## 세션 의도 원장 계약

Core 코드는 [Canonical intake provenance](../../../session-intent-analyzer/references/ledger-schema.md#canonical-intake-provenance)에 따라 현재 입력을 기록합니다. 이 절이 호스트별 입력 선택, 대기 입력의 판정과 실패 처리를 정의합니다. 응답 모델은 [session-intent-analyzer](../../../session-intent-analyzer/SKILL.md)에 따라 변경된 입력 해석과 정정의 출처를 기록합니다.

원장을 사용하는 실행 코드는 세션 식별 정보의 정확한 root·platform·session 값과 Core 코드가 의도 원장에 현재 입력으로 기록한 입력으로 공개 API를 호출합니다. 의도 원장의 SQLite 데이터베이스가 기준 저장소입니다. intent-state.json과 intent-events.jsonl은 가져오기·내보내기 형식이며 current-session.json은 참고용 포인터입니다. 어느 파일도 기록 대상 식별자를 대신하지 않습니다. 기록이 없으면 의존 작업은 미해결로 남습니다. 응답 모델은 인간 사용자의 목적이나 필수 의존 관계가 요구하고 인간 사용자의 지시가 허가한 범위 안에 있는 복구만 선택하며, 식별자를 추측하거나 없는 권한을 가정하지 않습니다.

[원장 스키마](../../../session-intent-analyzer/references/ledger-schema.md)는 상태 필드·트랜잭션 규칙·이관 해시·현재 작업 선택 기록을 정의합니다. 접수 이벤트와 intent-state 필드에는 원문 프롬프트·전체 대화·도구 출력·시스템/개발자 지침·비밀 값을 저장하지 않습니다. Core 코드는 응답 검토 절차에 따라 별도의 Core 검토 테이블(`core_completion_candidates`, `core_completion_assessments`, `core_completion_responses`)에 응답 모델의 답변·원본 출처 스냅샷과 응답 모델의 검토 판단을 저장합니다. 이 검토 기록은 응답 모델의 입력 해석과 구별됩니다. 훅 없는 작업도 같은 식별 계약을 따르며, 바로 답하는 답변은 도구를 사용하지 않습니다.

## 런타임 훅 그래프 계약

1. session-intent-analyzer의 SessionStart 훅이 세션 식별 정보를 제공합니다. 호스트의 프롬프트 제출 이벤트(`UserPromptSubmit`)에 등록된 session-intent-analyzer 훅은 호스트가 `user` 역할로 전달한 각 프롬프트를 관찰하고, 세션 식별 정보가 전달되지 않았거나 접수가 실패했을 때만 모델 문맥을 추가합니다.
2. 응답 모델은 `normal` 작업 선택 기록 후보를 Core 코드에 제출합니다. Core 코드는 Core 코드가 현재 입력으로 기록한 입력, 소유권, `selectable` 값과 선택한 완료 조건을 검증한 뒤 `ready` 상태의 작업 선택 기록을 저장합니다. 완료 조건의 `selectable` 값은 Core 코드가 출처 표지에서 정한 기본값이거나 응답 모델이 지정한 값이며, 응답 모델은 각 작업 단위의 `selectable` 값도 선언합니다. 값이 true이면 그 항목을 선택할 수 있으며, 그 값이 인간 사용자의 승인을 증명하지는 않습니다.
3. 응답 모델은 요구되는 작업 범위 선언과 담당 절차를 따릅니다. [책임과 흐름](../../../architecture_ko.md#책임과-흐름)이 도구 실행과 권한 집행을 호스트에 배정합니다. io-trace 훅은 도구 작업을 감사 기록(`~/.ghost-alice/io-trace.jsonl`)에 계속 남깁니다. 호스트는 이 훅을 Claude Code에서는 `PostToolUse`와 `PostToolUseFailure`에서, Codex에서는 `PostToolUse`에서 실행합니다. 호스트의 권한 규칙이나 훅 실행기 밖에 등록된 훅이 거부했거나, 호스트의 권한 확인 창에 거부한다는 답이 입력되어 호스트가 실행 전에 거부한 호출은 Claude Code와 Codex 모두에서 감사 기록에 없습니다. 인간 사용자가 실행 중에 취소한 호출도 감사 기록에 없습니다. Claude Code에서 거부된 호출에는 파일에 없는 `old_string`을 지정한 `Edit`이 포함됩니다. Codex에서 종료 코드가 0이 아닌 셸 명령은 감사 기록에 있지만, 실패한 `apply_patch`나 MCP 호출, 시작하지 못한 셸 명령 같은 그 밖의 실패한 호출은 감사 기록에 없습니다. WebSearch 같은 서버 쪽 도구도 Codex에서는 감사 기록에 없습니다.
4. 모델이 답변을 마쳐 Stop이 발생하면 Core 코드의 Stop 스크립트가 [훅 호출](../../../architecture_ko.md#훅-호출)의 설명대로 실행됩니다.

tool-checkpoint 훅은 도구 호출 전 단계(호스트의 `PreToolUse`/`BeforeTool`)에서 실행되며 Core 코드의 입력 접수가 아닙니다. 도구 입력이나 감사 메타데이터는 네이티브 권한을 부여하지 않습니다. [참고](#참고) 절은 설치된 바이트와 네이티브 실행을 서로 다른 주장으로 나열하며, 필수 실행 관찰은 [실환경 점검 회귀 테스트](live-smoke-regression.md)가 정합니다.

## 동적 판단 범위 계약

응답 모델은 [책임과 흐름](../../../architecture_ko.md#책임과-흐름)이 정의한 micro·meso·macro·meta 가운데에서 다음 판단 대상을 선택합니다. 조사가 요구되면 근거에 따라 판단 범위를 넓히거나 좁히거나, 같은 범위의 다른 대상으로 이동할 수 있습니다. meta 검토 자체가 사용자 질문을 요구하지는 않습니다.

작업 선택을 변경해야 할 때 응답 모델은 영향받은 결과와 반대 근거로 task-router를 다시 적용합니다. 결과를 그 대조 기준과 비교하고, 인간 사용자의 목적이나 필수 의존 관계가 요구하며 인간 사용자의 지시가 허가한 범위 안에 있는 수정을 선택할 수 있을 만큼 원인을 조사합니다. 인간 사용자의 목적과 필수 의존 관계에 영향이 없으면 해당 사항을 미해결로 남기거나 비필수 작업을 제외할 수 있으며, 불일치가 있다는 이유만으로 복구 작업을 생성하지 않습니다. 필수 조건은 전체 결과를 포함해 여전히 적극적인 근거를 요구합니다. 어떤 검사가 필수인지와 추가 검사를 언제 실행하는지는 [Evidence selection and stop gate](../../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate)가 정합니다. 정적인 `calls` 그래프는 반복 런타임 검사를 지시하지 않습니다.

## routing-surface 계약

응답 모델은 session-intent-analyzer를 적용하여 Python 원장 기록기를 통해 목적(`current_goal`)·범위(`latest_scope`)·제약·결정·질문·완료 조건을 의도 원장에 기록합니다. 항목마다 응답 모델이 기록한 출처 표지로 출처를 밝히며, 출처 표지가 없는 항목은 출처가 기록되지 않습니다. 응답 모델은 task-router를 적용하여 작업을 판단합니다. routing-surface는 응답 모드, boundary-contract 제어, 선택적 이유만 담고, 깊이나 복잡도 값은 담지 않습니다. 알 수 없거나 모순되는 값이 있으면 응답 모델은 영향받은 선택을 다시 판단합니다.

task-router를 따르는 응답 모델은 인간 사용자가 주로 요청한 것과 인간 사용자의 최종 목적을 보존합니다. 조사나 worktree 확인은 인간 사용자가 산출물로 요청하지 않았다면 수단입니다.

[경로 세부](../../../task-router/references/routing-details.md)는 바로 답하기와 확인 질문을 작업과 구별합니다. 일반적인 이유·방법 질문의 전제나 열린 작업 공간만으로 로컬 진단을 허가하지 않습니다. 확인 질문은 공용 계약의 Session Intent 절이 정한 형식을 따릅니다. 바로 답하기의 조건은 공용 계약의 [Work Selection](../../../platforms/common-governance.md#work-selection) 절이 정하며, 그 답은 도구를 사용하지 않고 작업 선택 기록을 등록하지 않습니다. 이 조건은 task-router가 작업 완료 경로에서 요구하는 작업 선택 기록 등록을 금지하지 않습니다.

## Work-Impact Projection 계약

훅은 값이 인간 사용자의 지시가 허가한 작업 안에서 응답 모델이 작업 범위 선언에 선언한 내용, 판단 범위, 검증 부담, 복구를 변경하는지에 따라 값을 분류합니다.

- 강제·위험·게이트 값과 검증 실패는 전체 모델 안내와 강제 표시를 유지합니다.
- 평상시 값은 훅 로그(`~/.ghost-alice/session-logs/<host>/<session>/strict-hook-output.jsonl`)에 남고, 작업 판단에 영향을 줄 때만 모델 안내에 포함됩니다.
- 알 수 없거나 실패한 값은 더 많은 문맥을 표시하고 해당 판단을 다시 검토합니다.

정상 통과 안내·상관관계 메타데이터만으로 새 작업을 만들지 않습니다.

## 런타임 체크포인트

결정에 영향을 주는 라우팅 안내는 [task-router](../../../task-router/SKILL.md#referenced-contracts)가 담당합니다.

[Verification-before-completion](../../../coding-convention/verification-before-completion/SKILL.md#completion-check-format)은 공통 `[completion-check]`·`[partial-check]` 형식과 기준·주장·근거 대응을 담당합니다. Core는 현재 소유한 작업 선택 기록에 연결된 주장·원본 자료·응답 전체의 검토가 인증되어야 완료 제출을 수락합니다. 스킬 로드는 절차 안내와 감사 활동이며 별도의 완료 선행 조건이 아닙니다. 최종 완료는 미검증 기준이 없고 모든 판정을 근거가 뒷받침할 것을 요구합니다. 부분 성공 보고는 전체 계약을 유지하고 미해결 기준을 밝히며, 성공한 부분 결과를 전체 작업의 완료로 제시하지 않습니다.

화면에 보인 기록은 증거가 되지 않습니다. 훅은 화면 표시와 관계없이 감사 기록을 남기고, 응답 모델은 각 주장을 실제 절차의 근거로 뒷받침합니다.

## tool-checkpoint 표시

공용 계약의 io-trace Transparency Rule이 체크포인트 표시를 맡습니다.


## 참고

절차 담당자를 지정하면 중복 규칙이 서로 어긋나지 않습니다. 응답 모델은 [지침 본문 재사용](../../../coding-convention/verification-before-completion/references/verify-or-reuse.md)을 따르고 실제 조회 또는 유효한 재사용을 내부에 기록합니다. 일상적인 정상 상태는 안내 문장을 반복하지 않으며, 대기·변경·실패 상태는 판단에 영향을 주는 안내를 유지합니다.

소스 수정, 지정 검사 통과, 설치된 바이트, 네이티브 실행, 기록된 검토와 보이는 답변은 각각 다른 주장입니다. 한 단계의 근거가 다른 단계를 입증하지는 않습니다.

## 완료 기록 계약

새 완료 또는 부분 완료 제출에서 Core Python 코드는 증거 형식과 인증된 의미 검토보다 현재 소유 경로를 먼저 확인합니다. 따라서 경로가 없으면 증거 필드 수정을 요구하기 전에 경로 오류를 반환합니다.

이 조건에서는 응답 모델이 [담당 검토 절차](../../../coding-convention/verification-before-completion/references/response-review.md#prepare-and-record-the-response-review)에 따라 두 판단을 기록합니다. [Core 응답 흐름](../../../architecture_ko.md#근거와-네이티브-표시)은 인증과 네이티브 답변 전달을 설명하며, [출력 의미](tool-output-semantics.md#검토출력계속-작업)는 근거가 뒷받침하는 결과와 뒷받침하지 않는 결과를 구분합니다.

소스 API·실행 모듈·원장·프로젝트·설치 스킬·플랫폼 상태는 별도의 위치 연결을 유지합니다. 일반 업그레이드 뒤에도 의도 원장의 SQLite 데이터베이스가 기준 저장소로 남으며, 전송 방식은 출처·세션·선택 가능 여부·승인을 부여하지 않습니다.

네이티브 Stop의 실행은 [훅 호출](../../../architecture_ko.md#훅-호출)이 설명합니다. 뒷받침됨·뒷받침되지 않음·미해결 상태는 서로 구별됩니다. 근거가 있는 보고도 작업 기준을 충족하지 않을 수 있습니다.

## 현재 선택과 한정된 복구

Core 코드는 task-router를 따르는 응답 모델이 제출한 현재 작업 선택 기록을 검증합니다. Core 코드가 현재 입력으로 기록한 다음 입력은 작업 선택 기록의 연결을 변경하되, 안정된 작업 단위와 변경 없는 원본 근거를 보존합니다.

작업 선택 기록·근거·기록에 결함이 확인되면 응답 모델은 아래의 담당 절차를 따릅니다. 응답 모델은 인간 사용자의 목적이나 필수 의존 관계가 요구하고 인간 사용자의 지시가 허가한 범위 안에 있을 때만 수정을 선택합니다. 뒷받침되지 않는 주장은 수정하거나 철회하며, 답변은 미해결 필수 기준을 미해결로 보고합니다. 변경 없는 동일한 기록 실패가 반복되면 그 기록 시도를 보류하되 업무를 재실행하거나 인간 사용자의 지시가 허가한 독립 작업을 막지 않습니다.

[검토·전달 절차](../../../coding-convention/verification-before-completion/references/response-review.md)가 호출 순서를 담당합니다. Core의 과거 결과 보고 경로는 원본 근거가 정확한 문장 전체를 뒷받침할 때만 적용하며, 추가 문구나 더 넓은 주장에는 별도 검토가 필요합니다.

## 수정과 권한 조건

- [Boundary-contract: Authority and renewal](../../../boundary-contract/SKILL.md#authority-and-renewal)은 인간 사용자의 지시가 부여한 권한·허용 대상·자원 제한을 담당합니다.
- [Systematic-debugging: Incident accounting and continuation](../../../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation)은 실패한 수정 회차·재평가·한정된 계속 진행을 담당합니다.
- [Verification-before-completion: Evidence Selection And Stop Gate](../../../coding-convention/verification-before-completion/SKILL.md#evidence-selection-and-stop-gate)는 유용한 검사와 근거 재사용을 담당합니다.
- [답변 검토 절차](../../../coding-convention/verification-before-completion/references/response-review.md)는 검토 기록·전달·기록 복구를 담당합니다.

담당 절차는 누적 시도, 원본 출처와 시각, 영향받지 않은 기준을 보존합니다. 한 작업 분기의 실패는 그 의존 작업만 막으며 인간 사용자의 지시가 허가한 다른 작업을 막지 않습니다.
