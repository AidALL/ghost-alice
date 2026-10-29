# Evaluator Artifact Contract

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/evaluator-artifact-contract.md)

This artifact contract applies to claims that adversarial-verification reviewed at level-3-high-stakes, intake of external agent-governance logic, and RAG/evaluator-driven improvement candidates. It preserves promotable evidence together with rejected candidates.
## Contents

- [Applicability](#applicability)
- [Artifact Set](#artifact-set)
- [Promotion Rules](#promotion-rules)
- [Minimum verifier-result.json Shape](#minimum-verifier-resultjson-shape)
- [Forbidden](#forbidden)


## Applicability

- Claims that adversarial-verification reviewed at level-3-high-stakes
- Intake of external agent-governance logic
- RAG or evaluator output that proposes playbook, rule, skill, or hook changes
- Promotion decisions before installed assets change

## Artifact Set

This contract defines the review inputs and the recorded decision; it does not provide a verifier launcher. The evaluation owner must give the chosen review tool explicit access to the artifacts and the criteria being judged, then retain its returned judgment. A filename alone does not send content to a reviewer. The promotion owner reads that judgment before changing installed assets.

The evaluation workflow supplies the scenario, execution trace, report and candidate procedure to the verifier. The verifier records its accepted or rejected judgment in `verifier-result.json`; the promotion owner reads that result before any installed-asset change.

- `scenario.json`: records the verification scenario, input conditions, non-goals, and forbidden surface.
- `trace.json`: records evaluator execution trace, read-only status, accessed sources, and command summaries.
- `report.json`: records metrics, findings, pass/fail state, and uncertainty.
- `candidate-playbook.md`: explains the candidate procedure for a human reviewer.
- `verifier-result.json`: records verifier judgment, accepted/rejected state, and rejected candidate list.

## Promotion Rules

- A read-only evaluator pass must not mutate installed assets.
- Do not promote `candidate-playbook.md` without an accepted verifier-result (`verifier-result.json`).
- Required verification includes at least one candidate rejected for invalid input or missing evidence. That result supports refusal for that candidate and condition only; it does not establish the verifier’s general ability or overall quality. Reuse valid unchanged case evidence; every edit does not require a new rejected candidate.
- If `verifier-result.json` has `accepted: false`, stop installed asset changes.

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
