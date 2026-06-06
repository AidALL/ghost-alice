# Evaluator Artifact Contract

언어: [🇺🇸 English](../../policies/evaluator-artifact-contract.md) | 🇰🇷 한국어

이 artifact contract는 verification-complexity-level-3-plus work, external agent-governance logic intake, RAG/evaluator 기반 개선 후보에 적용된다. report를 더 찍어내려는 게 아니라, rejected candidate와 함께 promote할 수 있는 evidence를 남기는 것이 목적이다.
## Contents

- [Applicability](#applicability)
- [Artifact Set](#artifact-set)
- [Promotion Rules](#promotion-rules)
- [Minimum verifier-result.json Shape](#minimum-verifier-resultjson-shape)
- [Forbidden](#forbidden)


## Applicability

- verification-complexity-level-3-plus work
- external agent-governance logic intake
- playbook, rule, skill, hook 변경을 제안하는 RAG 또는 evaluator output
- installed assets가 바뀌기 전의 promotion decision

## Artifact Set

- `scenario.json`: verification scenario, input condition, non-goal, forbidden surface를 기록한다.
- `trace.json`: evaluator execution trace, read-only 여부, accessed source, command 요약을 기록한다.
- `report.json`: metric, finding, pass/fail 상태, uncertainty를 기록한다.
- `candidate-playbook.md`: candidate 절차를 human reviewer에게 설명한다.
- `verifier-result.json`: verifier 판단, accepted/rejected 상태, rejected candidate 목록을 기록한다.

## Promotion Rules

- read-only evaluator pass는 installed assets를 mutate하면 안 된다.
- accepted verifier-result(`verifier-result.json`) 없이 `candidate-playbook.md`를 promote하지 않는다.
- verifier가 no라고 말할 수 있음을 증명하려면 rejected candidate가 최소 하나 있어야 한다.
- `verifier-result.json`에 `accepted: false`가 있으면 installed asset 변경을 멈춘다.
- 이 artifact contract는 high-risk work용이다. low-complexity task에서 report를 만들려고 쓰지 않는다.

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
