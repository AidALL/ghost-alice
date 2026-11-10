---
name: adversarial-verification
description: "Assigns independent delegates to try to refute a claim and its evidence and returns a verdict with the part that survives, when the human user or another skill calls for an adversarial review or fact-check of a numeric, legal, novelty or prior-art claim."
calls:
compatibility:
  - "Python 3.11+ standard library"
---

# Adversarial Verification

The response model uses this skill to coordinate an adversarial review when a skill, a policy or project instructions assign a claim that carries evidence to that review. Independent delegates challenge the claim, its evidence and the reasoning of earlier rounds. The response model records the verdict and the limit of what the evidence supports; agreement alone does not establish truth.

## Roles and input

The response model normalizes each claim, supplies the evidence, its source locators and the relevant schema or authoritative source, assigns the verification burden, and identifies claims about external tools. It assigns the attack roles, enforces the round protocol and returns a verdict. Following a domain procedure does not exempt the response model from a required adversarial review.

The response model supplies:

- claim-card and evidence;
- schema-context when relevant;
- verification-complexity: level-1-simple, level-2-evidence-required or level-3-high-stakes;
- domain-axes;
- optional consensus-rule: unanimity (default), majority or weighted;
- optional upstream-note on prior search, experiment or normalization.

## Entry gate

The response model admits claims marked as required or recommended for verification, claims that use external documents, legal conditions, numbers or sources outside the context that the human user approved, and claims of guarantees, compatibility, novelty, prior-art difference, reproducibility, theoretical result or experimental effect.

Before starting delegates, the response model applies these bypass conditions:

- Level 1 uses evidence precheck only: accept when sufficient evidence exists; otherwise reject or escalate.
- A self-identical fixed fact, a plan, a question or other non-assertive statement, or an internal calculation for which external comparison is irrelevant returns accept with zero rounds.

The entry gate limits cost. After admitting a claim, the response model must not reduce the required variety of attacks because the result seems obvious or the review seems expensive.

## Normalize the claim

The claim-card includes claim-id, claim-text, claim-type (`empirical | theoretical | methodological | composition | prior-art`), abstraction-level (`theory | method | experiment | system | product`), guarantees, non-guarantees, assumptions, possible-conflicts and evidence-list.

The response model rejects a claim with an empty evidence-list, removes inaccessible evidence and rejects the claim if no evidence remains. For a theoretical, composition or prior-art claim, missing guarantees, non-guarantees or assumptions require escalation. An unclear abstraction level also requires escalation.

Every source follows the source-locator contract in verification-before-completion's `references/external-tool-evidence.md`.

## Assign independent delegates

Level 2 uses three delegates: internal-logic, external-fact and internal-fact. Level 3 adds two distinct roles selected from external-logic, edge-case, prior-art and incentive according to the claim and domain. `references/agent-roles.md` defines their axes and assignment constraints.

In a research review, the assigned delegates distinguish guarantees from non-guarantees, test assumptions and abstraction levels, check composition interfaces and challenge inflated wording that a result is the first or the only one.

## Run and judge rounds

The response model follows [the round protocol and host dispatch procedure](references/round-protocol.md#dispatch-and-collect-through-the-host), supplying the complete role assignment and retaining each returned delegate handle with its axis. The round protocol defines which delegates take part in the first cycle and which affected axes follow up.

After each cycle, the proponent may supply new evidence. The response model runs the mandatory utterance audit.

The response model retains each locator, assumption and guarantee while updating the claim-card. Repeated attacks without a new distinction require narrowing the claim or escalating it.

After collecting the required submissions, apply [convergence-rules.md](references/convergence-rules.md). When its state is `accept` or `reject`, return the accepted or rejected part of the claim; when its state is `partial`, run the next round; when its state is `judge-deadlock`, use the deadlock handoff below.

## Deadlock and output

At deadlock, including the round cap, the response model has a fresh delegate summarize the dispute. That delegate does not decide the claim. It reports the agreed and unresolved subclaims, each issue, each side's supporting evidence and final attack, the cause of the deadlock, and the missing information or user-owned decisions. The response model keeps a factual disagreement unresolved with verdict `escalate`; lack of convergence cannot become acceptance. It asks the user only when the next action needs missing human authorization or a user-owned choice, using `escalate-human` for that condition. A human choice determines the authorized action or scope, not factual support. The response model preserves unmet claim criteria.

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

For `reject`, include the rejection reasons and the retracted claims. For `escalate`, identify the missing fields, evidence or decisions. For `accept`, keep the surviving claim and its assumptions; do not endorse the original overbroad wording.

## External-tool and evaluator requirements

For a material claim about an external tool, the response model loads verification-before-completion's `references/external-tool-evidence.md` from the installed skill and supplies its evidence requirements and the exact claim scope in each delegate's assignment.

For a claim reviewed at level-3-high-stakes, external governance absorption or evaluator-candidate promotion, follow `docs/policies/evaluator-artifact-contract.md` for its required artifacts and accepted result. The response model retains the returned verifier result and its supported scope; this skill does not redefine the evaluation protocol.

## Gotchas

The response model must not skip the entry gate, duplicate an attack axis, infer truth from unaudited agreement, or merge theory, experiment and system guarantees into one unsupported claim. A concession must identify which evidence or attack was defeated and how; pressure to finish is not evidence.
