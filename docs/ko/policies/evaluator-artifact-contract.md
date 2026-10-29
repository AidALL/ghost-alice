# Evaluator Artifact Contract

Language: [🇺🇸 English](../../policies/evaluator-artifact-contract.md) | 🇰🇷 한국어

이 산출물 계약은 adversarial-verification이 level-3-high-stakes로 검토한 주장, 외부 에이전트 거버넌스 논리 도입, RAG·평가 도구를 통한 개선 후보에 적용됩니다. 평가 담당자는 채택 가능한 근거와 거부된 후보를 함께 보존합니다.

## Contents

- [Applicability](#applicability)
- [Artifact Set](#artifact-set)
- [Promotion Rules](#promotion-rules)
- [Minimum verifier-result.json Shape](#minimum-verifier-resultjson-shape)
- [Forbidden](#forbidden)


## Applicability

- adversarial-verification이 level-3-high-stakes로 검토한 주장
- external agent-governance logic intake
- playbook, rule, skill, hook 변경을 제안하는 RAG 또는 evaluator output
- installed assets가 바뀌기 전의 promotion decision

## Artifact Set

이 계약은 검토 입력과 기록할 판단을 정의하며 검증 실행기를 제공하지는 않습니다. 평가 담당자는 선택한 검토 도구에 산출물과 판단 기준을 명시적으로 전달하고 반환된 판단을 보존해야 합니다. 파일 이름만 적는 것으로 검토자에게 내용이 전달되지는 않습니다. 적용 담당자는 설치 파일을 바꾸기 전에 그 판단을 읽습니다.

평가 워크플로는 시나리오, 실행 기록, 결과 보고서와 후보 절차를 검증 담당자에게 전달합니다. 검증 담당자는 `verifier-result.json`에 수락·거부 판단을 기록합니다. 변경 적용 담당자는 설치 파일을 바꾸기 전에 이 결과를 확인합니다.

- `scenario.json`: 검증 시나리오, 입력 조건, 제외 목표와 금지 대상을 기록합니다.
- `trace.json`: 평가 도구 실행 기록, 읽기 전용 여부, 접근한 출처와 명령 요약을 기록합니다.
- `report.json`: 지표, 발견 사항, 통과·실패 상태와 불확실성을 기록합니다.
- `candidate-playbook.md`: 사람 검토자에게 후보 절차를 설명합니다.
- `verifier-result.json`: 검증 담당자의 판단, 수락·거부 상태와 거부된 후보 목록을 기록합니다.

## Promotion Rules

- 읽기 전용 평가 중에는 평가 담당자가 설치 파일을 변경하지 않습니다.
- 적용 담당자는 `verifier-result.json`의 수락 판단 없이 `candidate-playbook.md`를 실제 절차에 반영하지 않습니다.
- 요구되는 검증에는 무효 입력이나 근거 누락 때문에 거부된 후보를 최소 하나 포함합니다. 해당 결과는 그 후보·조건에서의 거부 동작만 뒷받침하며 검증 모델의 일반 능력이나 전체 품질을 입증하지 않습니다. 변경 없는 유효한 사례 근거를 재사용할 수 있으며, 모든 편집에 새 거부 사례를 만들 필요는 없습니다.
- 적용 담당자는 `verifier-result.json`에 `accepted: false`가 있으면 설치 파일 변경을 멈춥니다.

## Minimum verifier-result.json Shape

```json
{
  "accepted": false,
  "reason": "insufficient evidence",
  "accepted_candidates": [],
  "rejected_candidates": [
    {
      "id": "candidate-001",
      "reason": "mutates installed assets during read-only pass"
    }
  ]
}
```

## Forbidden

- read-only pass 중 installed assets mutation
- rejected candidate 없이 모든 candidate를 accept하는 formal verifier flow
- evidence 없이 evaluator output을 skill, hook, rule에 바로 promote
- report를 만드는 것만이 목적인 report loop 생성
