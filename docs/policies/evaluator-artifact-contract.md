# Evaluator Artifact Contract

[🇺🇸 English](./evaluator-artifact-contract.md) | [🇰🇷 한국어](../ko/policies/evaluator-artifact-contract.md)

This artifact contract applies to claims that adversarial-verification reviewed at level-3-high-stakes, intake of external agent-governance logic, and RAG/evaluator-driven improvement candidates. It requires that promotable evidence and rejected candidates both be preserved.
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
- Promotion decisions that the human user makes before installed assets change

## Artifact Set

This contract defines the review inputs and the recorded judgment; it does not provide a verifier launcher. The response model that runs the evaluation must give the verifier (the review tool or delegate that runs the verification) explicit access to the artifacts and the evaluation criteria, then retain the judgment it returns. A filename alone does not send content to a reviewer. The human user, or the response model or a delegate carrying out a promotion that the human user decided, reads that judgment before changing installed assets.

The response model that runs the evaluation supplies the scenario, execution trace, report and candidate procedure to the verifier. The verifier records its judgment as the `verifier_accepted` value (`true` or `false`) in `verifier-result.json`; the human user, or the response model or a delegate carrying out a promotion that the human user decided, reads that result before any installed-asset change.

- `scenario.json`: records the verification scenario, input conditions, non-goals, and forbidden surface.
- `trace.json`: records evaluator execution trace, read-only status, accessed sources, and command summaries.
- `report.json`: records metrics, findings, pass/fail state, and uncertainty.
- `candidate-playbook.md`: explains the candidate procedure for a human reviewer.
- `verifier-result.json`: records the verifier's judgment as the `verifier_accepted` value (`true` or `false`) and the rejected candidate list.

## Promotion Rules

The response model reads an older `accepted` field as `verifier_accepted` and `accepted_candidates` as `verifier_accepted_candidates`. Equal old and new values describe one judgment; conflicting values leave that judgment unresolved and prevent the installed-asset change that depends on it. Neither field records the human user's authorization. Preserve the original result file when reading it.

- During a read-only evaluation pass, neither the response model that runs the evaluation nor any evaluator tool or delegate it runs changes installed assets.
- The human user, or the response model or a delegate carrying out a promotion that the human user decided, does not promote `candidate-playbook.md` without the verifier's `verifier_accepted: true` judgment in `verifier-result.json`.
- Required verification includes at least one candidate rejected for invalid input or missing evidence. That result supports refusal for that candidate and condition only; it does not establish the verifier’s general ability or overall quality. Unchanged case evidence follows the [evidence reuse rule](../../architecture.md#evidence-and-native-delivery).
- The human user, or the response model or a delegate carrying out a promotion that the human user decided, stops installed asset changes if `verifier-result.json` has `verifier_accepted: false`.

## Minimum verifier-result.json Shape

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

## Forbidden

- Mutating installed assets during a read-only pass
- Formal verifier flow that accepts every candidate without rejected candidates
- Promoting evaluator output directly into a skill, hook, or rule without evidence
- Creating report loops whose purpose is only to create reports
