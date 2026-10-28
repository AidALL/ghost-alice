---
name: adversarial-verification
description: "Assigns independent agents to attack a claim and its evidence and returns a verdict with the surviving claim, when the user or a calling procedure asks for adversarial review or fact-checking of a numeric, legal, novelty or prior-art claim."
calls:
compatibility:
  - "Python 3.11+ standard library"
---

# Adversarial Verification

The current-session assistant coordinates this review and uses this skill when governance assigns an evidence-bearing claim to adversarial review. Independent agents challenge the claim, evidence and prior-round reasoning. The coordinating assistant records the verdict and its supported boundary; agreement alone does not establish truth.

## Roles and input

The coordinating assistant normalizes claims, supplies evidence/source locators and relevant schema or source-of-truth context, assigns verification burden, and identifies external-tool claims. The coordinating assistant assigns attack roles, enforces the round protocol and returns a verdict. Following a domain procedure does not exempt that assistant from a required governance review.

The coordinating assistant supplies:

- claim-card and evidence;
- schema-context when relevant;
- verification-complexity: level-1-simple, level-2-evidence-required or level-3-high-stakes;
- domain-axes;
- optional consensus-rule: unanimity (default), majority or weighted;
- optional upstream-note on prior search, experiment or normalization.

## Entry gate

The coordinating assistant admits claims marked required/recommended for verification, claims using external documents/legal conditions/numbers or sources outside approved context, and claims of guarantees, compatibility, novelty, prior-art difference, reproducibility, theoretical result or experimental effect.

Before spawning agents, the coordinating assistant applies the existing bypass conditions:

- Level 1 uses evidence precheck only: accept when sufficient evidence exists; otherwise reject or escalate.
- A self-identical fixed fact, plan, question/non-assertive statement or internal calculation for which external comparison is irrelevant returns accept with zero rounds.

The entry gate controls cost. After admission, the coordinating assistant must not reduce required attack diversity because the result seems obvious or expensive.

## Normalize the claim

The claim-card includes claim-id, claim-text, claim-type (`empirical | theoretical | methodological | composition | prior-art`), claim-shape (fact/logic/mixed), abstraction-level (`theory | method | experiment | system | product`), guarantees, non-guarantees, assumptions, possible-conflicts, evidence-list and owner-approval-flag.

The coordinating assistant rejects an empty evidence-list, removes inaccessible evidence and rejects if none remains. For theoretical/composition/prior-art claims, missing guarantees, non-guarantees or assumptions requires escalation. An unclear abstraction level also requires escalation.

Every web source includes accessible_url, the supported finding/value and source-locator.source_type=web. Local/attached sources include file_path or filename, page, region (`top | middle | bottom | n/a`) and locator_note when needed. Numbers, tables, figures and source claims require exact locators.

## Assign independent agents

Level 2 uses three agents: internal-logic, external-fact and internal-fact. Level 3 adds two distinct roles selected from external-logic, edge-case, prior-art and incentive according to the claim and domain. `references/agent-roles.md` defines their axes and assignment constraints.

Each assigned review assistant attacks the claim on its assigned axis or explicitly reports no attack point with a reason. It may challenge another agent's prior-round verdict on any axis. Silence is not a verdict. Research review distinguishes guarantees from non-guarantees, tests assumptions and abstraction levels, checks composition interfaces and challenges inflated first/only novelty wording.

## Run and judge rounds

The coordinating assistant follows [the round protocol and host dispatch procedure](references/round-protocol.md#dispatch-and-collect-through-the-host), supplying the complete role assignment and retaining each returned agent handle with its axis. The round protocol defines first-cycle participation and affected-axis follow-up. Assigned agents receive the same current claim context without access to peers' current-cycle submissions.

After each cycle, the proponent may supply new evidence. An absent response supplies no new evidence, not automatic rejection. The coordinating assistant runs the mandatory utterance audit and performs a schema/source-of-truth checkpoint only when new evidence, changed schema or an observed mismatch can affect the verdict. The checkpoint supplies factual attack material; it does not mediate.

The coordinating assistant retains each locator, assumption and guarantee while updating the claim-card. A response that changes none of the surviving claim, assumptions or evidence is no new evidence. Repeated attacks without a new distinction require claim narrowing or escalation rather than paraphrased no-op rounds. Deleted source evidence or a material schema change produces escalate.

After collecting the required submissions, apply [convergence-rules.md](references/convergence-rules.md). Return its accepted/rejected boundary, continue only for its decision-relevant partial state, or use the deadlock handoff below.

## Deadlock and output

At deadlock, including the cap, the coordinating assistant delegates the dispute-summary subtask to a review assistant called the meta-judge. The meta-judge does not decide the claim. It reports agreed and unresolved subclaims, each issue, supporting evidence and final attack by side, deadlock cause, and missing information or user-owned decisions. The coordinating assistant retains a factual disagreement as unresolved with verdict `escalate`; lack of convergence cannot become acceptance. It asks the user only when the next action needs missing human authorization or a user-owned choice, using `escalate-human` for that condition. A human choice determines authorized action or scope, not factual support. The assistant preserves unmet claim criteria and continues independent authorized work.

The returned record uses:

```text
verdict: accept | reject | escalate | escalate-human
surviving-claim: <supported narrowed claim>
required-assumptions: <remaining assumptions>
guarantee-boundary: <what the claim guarantees and excludes>
rounds-log: <full log locator and summary>
retracted-claims: <withdrawn claims>
escalation-issues: <unresolved fields or human decisions>
```

For reject, include rejection reasons and retracted claims. For escalate, identify missing fields/evidence/decisions. For accept, retain the surviving claim and its assumptions rather than endorsing the original overbroad wording.

## External-tool and evaluator requirements

For material external-tool claims, the coordinating assistant loads verification-before-completion's `references/external-tool-evidence.md` from the installed skill and supplies its applicable category, evidence requirements and exact claim scope in each reviewer assignment. That reference owns query patterns and source-locator fields.

For level-3 evaluation, external governance absorption or evaluator candidate promotion, follow `docs/policies/evaluator-artifact-contract.md` for its required artifacts and accepted result. The coordinating assistant retains the returned verifier result and its supported scope; this skill does not redefine the evaluation protocol.

## Gotchas

The coordinating assistant must not skip the entry gate, put level 1 into rounds, duplicate an attack axis, infer truth from unaudited agreement, or merge theory/experiment/system guarantees into one unsupported claim. Concessions must identify which evidence or attack was defeated and how; pressure to finish is not evidence.
