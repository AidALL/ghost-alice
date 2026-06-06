# Evaluator Artifact Contract

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/evaluator-artifact-contract.md)

This artifact contract applies to verification-complexity-level-3-plus work, intake of external agent-governance logic, and RAG/evaluator-driven improvement candidates. Its purpose is not to produce more reports; it is to preserve promotable evidence together with rejected candidates.
## Contents

- [Applicability](#applicability)
- [Artifact Set](#artifact-set)
- [Promotion Rules](#promotion-rules)
- [Minimum verifier-result.json Shape](#minimum-verifier-resultjson-shape)
- [Forbidden](#forbidden)


## Applicability

- verification-complexity-level-3-plus work
- Intake of external agent-governance logic
- RAG or evaluator output that proposes playbook, rule, skill, or hook changes
- Promotion decisions before installed assets change

## Artifact Set

- `scenario.json`: records the verification scenario, input conditions, non-goals, and forbidden surface.
- `trace.json`: records evaluator execution trace, read-only status, accessed sources, and command summaries.
- `report.json`: records metrics, findings, pass/fail state, and uncertainty.
- `candidate-playbook.md`: explains the candidate procedure for a human reviewer.
- `verifier-result.json`: records verifier judgment, accepted/rejected state, and rejected candidate list.

## Promotion Rules

- A read-only evaluator pass must not mutate installed assets.
- Do not promote `candidate-playbook.md` without an accepted verifier-result (`verifier-result.json`).
- At least one rejected candidate must exist so the verifier has proven it can say no.
- If `verifier-result.json` has `accepted: false`, stop installed asset changes.
- This artifact contract is for high-risk work. Do not use it to manufacture reports for low-complexity tasks.

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

- Mutating installed assets during a read-only pass
- Formal verifier flow that accepts every candidate without rejected candidates
- Promoting evaluator output directly into a skill, hook, or rule without evidence
- Creating report loops whose purpose is only to create reports
