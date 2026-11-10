# 평가 산출물 계약

[🇺🇸 English](../../policies/evaluator-artifact-contract.md) | [🇰🇷 한국어](./evaluator-artifact-contract.md)

이 산출물 계약은 adversarial-verification이 level-3-high-stakes로 검토한 주장, 외부 에이전트 거버넌스 논리 도입, RAG·평가 도구를 통한 개선 후보에 적용됩니다. 이 계약은 승격 가능한 근거와 거부된 후보를 모두 보존하도록 요구합니다.

## 목차

- [적용 범위](#적용-범위)
- [산출물 묶음](#산출물-묶음)
- [승격 규칙](#승격-규칙)
- [verifier-result.json의 최소 형태](#verifier-resultjson의-최소-형태)
- [금지 사항](#금지-사항)


## 적용 범위

- adversarial-verification이 level-3-high-stakes로 검토한 주장
- 외부 에이전트 거버넌스 논리 도입
- 절차·규칙·스킬·훅 변경을 제안하는 RAG 또는 평가 도구 출력
- 설치 파일이 변경되기 전에 인간 사용자가 내리는 승격 결정

## 산출물 묶음

이 계약은 검토 입력과 기록할 판단을 정의하며 검증 실행기를 제공하지는 않습니다. 평가를 실행하는 응답 모델은 검증을 실행하는 검토 도구나 위임 에이전트에 산출물과 평가 기준을 명시적으로 전달하고 반환된 판단을 보존해야 합니다. 파일 이름만 기록해서는 검토자에게 내용이 전달되지 않습니다. 인간 사용자, 또는 인간 사용자가 결정한 승격을 수행하는 응답 모델이나 위임 에이전트는 설치 파일을 변경하기 전에 그 판단을 조회합니다.

평가를 실행하는 응답 모델은 시나리오, 실행 기록, 결과 보고서와 후보 절차를 검증을 실행하는 검토 도구나 위임 에이전트에 전달합니다. 검증을 실행하는 검토 도구나 위임 에이전트는 `verifier-result.json`에 `verifier_accepted` 판정(`true` 또는 `false`)을 기록합니다. 인간 사용자, 또는 인간 사용자가 결정한 승격을 수행하는 응답 모델이나 위임 에이전트는 설치 파일을 변경하기 전에 이 결과를 조회합니다.

- `scenario.json`: 검증 시나리오, 입력 조건, 제외 목표와 금지 대상을 기록합니다.
- `trace.json`: 평가 도구 실행 기록, 읽기 전용 여부, 접근한 출처와 명령 요약을 기록합니다.
- `report.json`: 지표, 발견 사항, 통과·실패 상태와 불확실성을 기록합니다.
- `candidate-playbook.md`: 사람 검토자에게 후보 절차를 설명합니다.
- `verifier-result.json`: 검증을 실행하는 검토 도구나 위임 에이전트의 `verifier_accepted` 판정(`true` 또는 `false`)과 거부된 후보 목록을 기록합니다.

## 승격 규칙

응답 모델은 옛 `accepted` 필드를 `verifier_accepted`로, `accepted_candidates`를 `verifier_accepted_candidates`로 읽습니다. 옛 필드와 새 필드의 값이 같으면 하나의 판정으로 읽습니다. 값이 다르면 응답 모델은 그 판정을 미해결로 남기고, 그 판정에 의존하는 설치 파일 변경을 수행하지 않습니다. 두 필드는 인간 사용자의 허가를 기록하지 않습니다. 응답 모델은 결과를 읽을 때 원본 파일을 보존합니다.

- 읽기 전용 평가 중에는 평가를 실행하는 응답 모델도, 그 응답 모델이 실행하는 평가 도구나 위임 에이전트도 설치 파일을 변경하지 않습니다.
- 인간 사용자, 또는 인간 사용자가 결정한 승격을 수행하는 응답 모델이나 위임 에이전트는 `verifier-result.json`의 `verifier_accepted: true` 판정 없이 `candidate-playbook.md`를 승격하지 않습니다.
- 요구되는 검증에는 무효 입력이나 근거 누락 때문에 거부된 후보를 최소 하나 포함합니다. 해당 결과는 그 후보·조건에서의 거부 동작만 뒷받침하며 검증을 실행하는 검토 도구나 위임 에이전트의 일반 능력이나 전체 품질을 입증하지 않습니다. 변경 없는 사례 근거는 [근거 재사용 규칙](../../../architecture_ko.md#근거와-네이티브-표시)을 따릅니다.
- 인간 사용자, 또는 인간 사용자가 결정한 승격을 수행하는 응답 모델이나 위임 에이전트는 `verifier-result.json`에 `verifier_accepted: false`가 있으면 설치 파일 변경을 중지합니다.

## verifier-result.json의 최소 형태

```json
{
  "verifier_accepted": false,
  "reason": "insufficient evidence",
  "verifier_accepted_candidates": [],
  "rejected_candidates": [
    {
      "id": "candidate-001",
      "reason": "mutates installed assets during read-only pass"
    }
  ]
}
```

## 금지 사항

- 읽기 전용 평가 중 설치 파일 변경
- 거부된 후보 없이 모든 후보를 수락하는 형식적 검증 흐름
- 근거 없이 평가 도구 출력을 스킬·훅·규칙으로 바로 승격
- 보고서 생성만이 목적인 보고서 루프 생성
