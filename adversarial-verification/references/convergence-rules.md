# Convergence Rules

This document defines the detailed rules for the convergence conditions of the adversarial-verification skill, the criteria for judging "semantic duplication", and edge case handling.

## Table of Contents

- [The 4 Convergence States](#the-4-convergence-states)
- [convergence-accept Detailed Conditions](#convergence-accept-detailed-conditions)
- [convergence-reject Detailed Conditions](#convergence-reject-detailed-conditions)
- [convergence-judge-deadlock Detailed Conditions](#convergence-judge-deadlock-detailed-conditions)
- [convergence-partial Detailed Conditions](#convergence-partial-detailed-conditions)
- [Semantic Duplication Judgment](#semantic-duplication-judgment)
- [Edge Cases](#edge-cases)

---

## The 4 Convergence States

| State | Meaning | Progress |
|------|------|------|
| accept | consensus rule passed + attacks exhausted | return Step 5 result |
| reject | claim defense failed | return Step 5 result |
| judge-deadlock | finite-cap or no-delta disagreement | invoke Step 4.5 meta-judge |
| partial | new attack points still emerging | continue rounds |

The convergence judgment is performed once at the end of each complete independent attack cycle, after the decision-relevant checkpoint and audit.

Stop when no decision-relevant uncertainty remains and another round cannot produce a relevant state delta. Do not continue only to satisfy a round-count minimum.

---

## convergence-accept Detailed Conditions

All conditions must be satisfied at the same time (AND).

1. every assigned agent completed the same full attack cycle independently
2. every agent declared "axis-attack: no attack point"
3. every agent declared "meta-attack: no meta attack", or no prior-round utterance existed as an eligible meta-attack target
4. the agent judgments pass the caller-specified consensus rule (unanimous / majority / weighted). When unspecified, the default is unanimous
5. sycophancy audit passed with 0 violations in that round
6. no unresolved decision-relevant uncertainty remains and another round cannot produce a relevant state delta

○ Consensus rule selection
- The consensus rule applies one of unanimous / majority / weighted according to domain policy
- If the caller does not specify a consensus rule, the default is unanimous
- Rationale for choosing unanimous: majority vote conflicts with the lower-bound guarantee philosophy. "2 accept · 1 reject" is a state where 1 potential problem remains. The moment you ignore that 1, the lower bound collapses
- Rationale for choosing majority/weighted: when domain policy explicitly decides the trade-off between processing speed and the lower-bound guarantee

---

## convergence-reject Detailed Conditions

Any one of the following (OR).

1. a decisive attack remains unresolved, the proponent supplies no decision-relevant new evidence, and the applicable consensus rule supports rejection
2. all items in the claim's evidence-list eliminated (judged in Step 1)
3. the human-proponent explicitly withdraws the claim

○ Same-attack judgment
- the same agent attacks the same target-claim-id with the same attack-type without adding a decision-relevant distinction
- the attack-content is semantically identical (paraphrase allowed, core argument identical)
- for the semantic-identity judgment, see "Semantic Duplication Judgment" below

○ No-new-evidence judgment
- the human-proponent did respond but had no evidence different from the prior round
- there is no response at all
- the response is on a different topic unrelated to the attack

---

## convergence-judge-deadlock Detailed Conditions

Any one of the following (OR). When triggered, call the Step 4.5 meta-judge.

1. the finite safety cap of 50 rounds is reached with convergence-accept / reject still not triggered
2. every agent declared "no attack point", no relevant state delta remains, and the final-verdict is split
  - example: agent-C-internal-logic is accept, agent-C-external-fact is fixed at reject, with no new attack on either side
  - in this case no new attack emerges, so running more rounds is meaningless
3. the human-proponent cannot supply a response or new evidence, the agent judgments are split, and no identified next check can change the state
  - the absence of a response is one form of deadlock

○ When the deadlock triggers
- condition 1: at the end of round 50
- conditions 2 and 3: immediately after the no-delta split state is established

Unresolved disagreement fails closed to the meta-judge and human escalation; it never becomes acceptance through elapsed rounds.

---

## convergence-partial Detailed Conditions

- one or more agents is still raising a decision-relevant new attack point, or new evidence changes the surviving-claim, assumptions, or guarantee boundary
- the named uncertainty can change the next verdict or required action

In this state no terminal convergence judgment is made, and it proceeds to the next round under the finite safety cap of 50 rounds.

---

## Semantic Duplication Judgment

The criterion that distinguishes "same attack" from "new attack". If this judgment is inaccurate, the entire set of convergence conditions collapses.

○ Same-attack judgment criteria (all AND)
1. the same target-claim-id
2. the same attack-type (logical-gap, contradiction, hidden-premise, etc.)
3. the core argument of the attack-content is identical (core argument = "which part of the claim is wrong and why")

○ Judgment procedure
- 1st pass: confirm target-claim-id and attack-type match (deterministic)
- 2nd pass: compare the core argument of the attack-content
  - do the two attacks point at the same part of the claim
  - do the two attacks assert the same error type
  - do the two attacks require the same evidence to be resolved
- if the 2nd pass is ambiguous, one auxiliary LLM judgment is allowed (recording the judgment is mandatory)

○ New-attack judgment criteria
- if even one of the 1st-pass or 2nd-pass criteria differs, it is a new attack
- judge strictly: when ambiguous, classify as a new attack. This is the safety bias that runs the rounds longer

○ Paraphrase allowance
- expressing the same attack in different words is the same attack
- explaining the same error from a different angle is a new attack

○ Examples
- attack 1 (round 3): "the claim's figure does not match the approved source record"
- attack 2 (round 5): "the revenue of the same claim differs from the internal financial record by 0.07 hundred million"
- judgment: same attack (same target, same attack-type, same core argument)

- attack 1 (round 3): "the claim's revenue of 1.55 hundred million does not match the SSOT"
- attack 2 (round 5): "the claim's revenue calculation has no pre-tax / post-tax distinction"
- judgment: new attack (same target but different attack-type)

---

## Edge Cases

### Agent judgment change

- an agent can change its final-verdict mid-round
- example: accept at round 5, reject at round 6
- a judgment change is a relevant state delta; continue only when it reopens a named uncertainty that another round can resolve

### human-proponent absent mode

- a mode where the caller skips collecting human responses
- the rounds proceed with only the initial evidence-list
- the no-response judgment triggers immediately, so the tendency toward convergence-reject is strong
- in this mode, convergence-judge-deadlock condition 3 (no response for 3 consecutive rounds) does not apply. Instead, the human-response-based convergence conditions are entirely lifted

### Agent replacement

- on a 3-round cumulative failure of the sycophancy audit, replace the agent
- the replaced agent is spawned anew. The previous agent's utterance record is kept, but its final-verdict is voided
- re-run the replaced axis against the current state, but do not extend unrelated axes only because replacement occurred

### Immediate convergence after a complete attack cycle

- if the convergence conditions are met at the end of the first complete independent attack cycle, converge immediately
- every assigned agent must participate, so level 2 still uses 3 independent agents and level 3 still uses 5 independent agents
- if an attack or response changes the evidence, surviving-claim, assumptions, or guarantee boundary, continue only to test that changed state

### Request to exceed the round cap of 50

- the case where the caller requests raising the cap further (for example, 100 rounds)
- not allowed. 50 is the balance point of cost and convergence likelihood
- if it does not converge even at 50 rounds, that problem cannot be solved with rounds. The meta-judge or a human must solve it

### Simultaneous claim verification

- when verifying multiple claims at once, each claim gets independent rounds
- the agents may be shared, but the rounds-log is separated per claim
- cross-attacks between claims are not allowed (to prevent confusion)
