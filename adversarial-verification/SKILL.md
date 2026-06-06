---
name: adversarial-verification
description: "Use when claims or evidence need adversarial validation by 3-5 independent agents. Requires unanimous convergence. Triggers: patents, papers, grants, investor relations, legal docs, numeric claims, novelty, prior-art, and fact-checking."
calls:
compatibility:
  - "Python 3.11+ standard library"
---

# adversarial-verification

adversarial-verification validates claims by making independent agents attack
the claim, the evidence, and each other's reasoning. It is a cross-cutting
governance engine, not a domain-specific optional helper.

Use it when a claim carries real evidence burden: legal, financial, research,
grant, patent, investor relations, prior-art, numeric, novelty, consistency, or
source-grounded statements.
## Contents

- [Core Principles](#core-principles)
- [Position In The System](#position-in-the-system)
- [Input Shape](#input-shape)
- [Output Shape](#output-shape)
- [Step 0: Entry Gate](#step-0-entry-gate)
- [Step 1: Normalize Claim Card](#step-1-normalize-claim-card)
- [Step 2: Assign Agents](#step-2-assign-agents)
- [Step 3: Adversarial Rounds](#step-3-adversarial-rounds)
- [Step 4: Convergence](#step-4-convergence)
- [Step 4.5: Meta-Judge](#step-45-meta-judge)
- [Step 5: Return Result](#step-5-return-result)
- [External Tool Claim Web Search](#external-tool-claim-web-search)
- [Evaluator Artifact Contract](#evaluator-artifact-contract)
- [References](#references)
- [Gotchas](#gotchas)


## Core Principles

1. Easy convergence is suspicious. For
   `verification-complexity-level-2` and above, do not check convergence before
   round 5.
2. There is no mediator during normal rounds. Agents attack, respond, and
   checkpoint against the claim card and evidence. A meta-judge appears only at
   deadlock.
3. Concessions must be evidence-bound. The only valid concession shape is:
   "my evidence X was specifically defeated by opposing evidence Y."
4. Three agents are the minimum for attack diversity. Two agents collapse too
   easily into attacker/defender roles.
5. Mixed agent verdicts default to deadlock/escalation unless the configured
   consensus rule is satisfied.
6. This skill is cross-cutting and injected by the governance layer. A domain
   skill does not choose whether to use it. Any claim that carries a
   verification grade must pass through this skill, and the choice belongs to
   the governance layer.
7. Cost is controlled at the entry gate, not by weakening the adversarial round
   once a claim is admitted.

## Position In The System

Caller responsibilities:

- normalize claim units
- provide evidence and source locators
- provide schema or SSOT context when needed
- decide which claims carry verification burden
- inject external-tool web-search requirements when Category B or C claims are
  present

This skill responsibilities:

- assign adversarial roles
- run structured adversarial rounds
- enforce convergence rules
- return `accept`, `reject`, or `escalate`
- preserve the surviving claim boundary and required assumptions

## Input Shape

- `claim-card`: one normalized claim
- `evidence`: source links, citations, local locators, or logical chains
- `schema-context`: caller-provided schema or SSOT snapshot
- `verification-complexity`: `level-1-simple | level-2-evidence-required | level-3-high-stakes`
- `domain-axes`: applicable domain axes
- `consensus-rule`: optional, `unanimity | majority | weighted`; default is
  `unanimity`
- `upstream-note`: optional note about prior search, experiment, or
  normalization scope

## Output Shape

- `verdict`: `accept | reject | escalate | escalate-human`
- `surviving-claim`: the narrow claim that survived attack
- `required-assumptions`: assumptions that remain required
- `guarantee-boundary`: what the claim does and does not guarantee
- `rounds-log`: all attacks and responses
- `retracted-claims`: claims withdrawn during rounds
- `escalation-issues`: unresolved issues for a human decision

## Step 0: Entry Gate

Admit a claim if any condition is true:

- claim has verification metadata marked required or recommended
- claim includes external documents, legal conditions, or numeric claims
- claim references information outside caller-approved source context
- claim asserts guarantees, state-of-the-art status, theory, component
  compatibility, novelty, prior-art difference, reproducibility, or
  experimental effect

Reject or bypass adversarial rounds when:

- `verification-complexity=level-1-simple`
- the claim is a self-identical fixed fact
- the claim is only a plan, question, or non-assertive statement
- caller-internal calculation makes external comparison irrelevant

For `level-1-simple`, perform evidence precheck only. Return `accept` when
evidence exists, otherwise `reject` or `escalate`.

For the other bypass conditions, a self-identical fixed fact, a plan or
non-assertive statement, or a caller-internal calculation, return `accept`
immediately with zero rounds.

## Step 1: Normalize Claim Card

Required fields:

- `claim-id`
- `claim-text`
- `claim-type`: `empirical | theoretical | methodological | composition | prior-art`
- `claim-shape`: fact, logic, or mixed
- `abstraction-level`: `theory | method | experiment | system | product`
- `guarantees`
- `non-guarantees`
- `assumptions`
- `possible-conflicts`
- `evidence-list`
- `owner-approval-flag`

Evidence precheck:

- Empty `evidence-list` means `verdict=reject`.
- Inaccessible evidence is removed.
- If all evidence is removed, return `reject`.
- If `claim-type` is `theoretical`, `composition`, or `prior-art` and
  `guarantees`, `assumptions`, or `non-guarantees` are missing, return
  `escalate`.
- If `abstraction-level` is unclear, return `escalate`.

Source-locator requirements:

- Web source: `accessible_url`, finding/value, and
  `source-locator.source_type=web`.
- Local or attached source: `file_path` or filename, `page`, `region`, and
  `locator_note` when needed.
- `region` enum: `top | middle | bottom | n/a`.
- Numeric values, tables, figures, and source claims need exact locators.

## Step 2: Assign Agents

`verification-complexity-level-2` uses 3 agents:

- `agent-C-internal-logic`
- `agent-C-external-fact`
- `agent-C-internal-fact`

`verification-complexity-level-3` uses those 3 plus 2 selected from:

- `agent-C-external-logic`
- `agent-C-edge-case`
- `agent-C-prior-art`
- `agent-C-incentive`

Each agent must attack from its axis or explicitly declare "no attack point".
Silence is not allowed. Agents may attack other agents' verdicts from any axis.

For a research claim, each agent especially checks:

- guarantees mixed with non-guarantees
- assumptions weaker than the conclusion, or missing
- different abstraction levels merged to exaggerate a result
- a composition claim that hides an interface conflict between parts
- novelty inflated with "first" or "only" wording

## Step 3: Adversarial Rounds

- One round means every agent speaks once against the same round context.
- Each response contains an attack on the claim or "no attack point", plus
  optional meta-attack on previous agent claims.
- The human proponent or caller may add evidence after each round. If no
  response is available, the initial evidence stays fixed.
- Rounds 1-4 are mandatory for level 2 and above.
- From round 5, apply convergence rules.
- Stop at round 50 and escalate.
- Every 5 rounds, checkpoint the claim-card and evidence-list against schema
  and SSOT. The checkpoint does not mediate; it only feeds mismatches back into
  the next attack round. Switch the verdict to `escalate` only when a serious
  mismatch appears, such as evidence deleted from the SSOT or a changed schema.

Common checkpoint each round, before the attacks:

- Re-collate the claim-card against the evidence-list and confirm no locator,
  guarantee, or assumption went missing.
- After a proponent response, check whether the new evidence actually changes
  the surviving-claim or the required-assumptions. If it does not, treat the
  round as no new evidence.
- If the same attack repeats, do not just reword it. Shrink the claim-card or
  switch to escalation.

The full speech constraints live in `references/round-protocol.md`.

## Step 4: Convergence

- `convergence-accept`: two consecutive rounds where all agents report no new
  attack point and no meta-attack, and the consensus rule passes.
- `convergence-reject`: the same attack repeats for two consecutive rounds and
  the proponent gives no new evidence or only repeats existing evidence.
- `convergence-judge-deadlock`: round 50 is reached, or agents report no new
  attack point but verdicts still conflict.
- `convergence-partial`: new attack points still exist; continue rounds.

"No attack point" means no semantically novel attack remains, not that an agent
is unwilling to attack further.

No majority vote is used unless the caller explicitly configured it.

## Step 4.5: Meta-Judge

The meta-judge appears only at deadlock and has no decision authority. At the
round cap it must appear, because silently escalating a deadlock without it
abdicates the verdict duty. It writes a `deadlock-report`:

- agreed subclaims
- unresolved subclaims
- one-sentence issue summary for each deadlock
- final supporting evidence by side
- final attack by side
- deadlock cause: missing evidence, evidence interpretation, domain expertise,
  value judgment, or other
- information or human judgment needed

Allowed meta-judge wording: "Issue X is deadlocked. Support uses evidence Y.
Opposition attacks with Z. Cause is W. Human decision required."

After the deadlock-report, the verdict becomes `escalate-human`. The caller
relays the report to a human and records the final accept or reject. This skill
does not collect the human decision; the human interface is the caller's
responsibility.

## Step 5: Return Result

For `accept`, return the surviving claim, required assumptions, guarantee
boundary, and rounds log.

For `reject`, return retracted claims and rejection reasons.

For `escalate`, return structured escalation issues and required missing fields
or decisions.

## External Tool Claim Web Search

For Category B or C claims about external tools, libraries, CLIs, SDKs,
frameworks, versions, or platform behavior, inject this requirement into every
adversarial agent prompt:

```text
Identify Category B/C external-tool claims. For each, run at least three
independent community-report searches: `<tool> github issue <year>`,
`<tool> reddit`, and `<tool> not working <version>`. Cite accessible URLs and
source-locators. Treat official-doc-only evidence as an unverified echo.
```

Without this injection, the adversarial result is not trusted for those claims.

## Evaluator Artifact Contract

For `verification-complexity-level-3` and higher, external governance logic
absorption, or RAG/evaluator-based candidate promotion, apply
`docs/policies/evaluator-artifact-contract.md`.

Required artifact set:

- `scenario.json`
- `trace.json`
- `report.json`
- `candidate-playbook.md`
- `verifier-result.json`

Read-only evaluator passes do not modify installed assets. Do not promote a
candidate without an accepted verifier result. At least one rejected candidate
must exist to prove the verifier can say no.

## References

- `references/agent-roles.md`
- `references/round-protocol.md`
- `references/convergence-rules.md`

## Gotchas

- Do not skip Step 0; otherwise cost explodes.
- `verification-complexity-level-1` never enters adversarial rounds.
- Do not use fewer than 5 rounds for level 2 or higher convergence checks.
- Do not assign two agents to the same axis.
- Agent agreement is not truth if speech constraints were ignored.
- Separate `guarantees` and `non-guarantees` or the round becomes wording
  debate.
- Do not mix theory, experiment, and system guarantees in one claim.
