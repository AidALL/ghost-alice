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
| judge-deadlock | 50-round stalemate | invoke Step 4.5 meta-judge |
| partial | new attack points still emerging | continue rounds |

The convergence judgment is performed once at the end of each round (after Phase C), starting from round >= 5.

---

## convergence-accept Detailed Conditions

All conditions must be satisfied at the same time (AND).

1. round >= 5
2. for 2 consecutive prior rounds, every agent declared "axis-attack: no attack point"
3. for 2 consecutive prior rounds, every agent declared "meta-attack: no meta attack"
4. the agent judgments pass the caller-specified consensus rule (unanimous / majority / weighted). When unspecified, the default is unanimous
5. sycophancy audit passed (0 violations in that round)

○ Why 2 consecutive rounds
- 1 round of "no attack point" could be chance or a lapse in focus
- 2 consecutive rounds is a signal that "there really is nothing to attack"
- No need to raise it to 3 or more rounds. 2 is the minimum confidence interval

○ Consensus rule selection
- The consensus rule applies one of unanimous / majority / weighted according to domain policy
- If the caller does not specify a consensus rule, the default is unanimous
- Rationale for choosing unanimous: majority vote conflicts with the lower-bound guarantee philosophy. "2 accept · 1 reject" is a state where 1 potential problem remains. The moment you ignore that 1, the lower bound collapses
- Rationale for choosing majority/weighted: when domain policy explicitly decides the trade-off between processing speed and the lower-bound guarantee

---

## convergence-reject Detailed Conditions

Any one of the following (OR).

1. the same attack for 2 consecutive rounds + no new-evidence response
2. all items in the claim's evidence-list eliminated (judged in Step 1)
3. the human-proponent explicitly withdraws the claim

○ Same-attack judgment
- the same agent attacks the same target-claim-id with the same attack-type for 2 consecutive rounds
- the attack-content is semantically identical (paraphrase allowed, core argument identical)
- for the semantic-identity judgment, see "Semantic Duplication Judgment" below

○ No-new-evidence judgment
- the human-proponent did respond but had no evidence different from the prior round
- there is no response at all
- the response is on a different topic unrelated to the attack

---

## convergence-judge-deadlock Detailed Conditions

Any one of the following (OR). When triggered, call the Step 4.5 meta-judge.

1. round = 50 reached, with convergence-accept / reject still not triggered
2. round >= 5 and every agent declared "no attack point" but the final-verdict is split
  - example: agent-C-internal-logic is accept, agent-C-external-fact is fixed at reject, with no new attack on either side
  - in this case no new attack emerges, so running more rounds is meaningless
  - however, even in this case it must be at least round >= 5 to trigger
3. the human-proponent response is "none" for 3 consecutive rounds and the agent judgments are split
  - the absence of a response is one form of deadlock

○ When the deadlock triggers
- condition 1: at the end of round 50
- condition 2: immediately after that state holds for 1 round (the round in which no attack point + split judgment is confirmed)
- condition 3: after confirmation across 3 consecutive rounds

---

## convergence-partial Detailed Conditions

- round >= 5
- 1 or more of the agents is still raising a new attack point (the declarers of "no attack point" are not all of them)
- the final-verdict is split

In this state no convergence judgment is made, and it proceeds to the next round. Up to the round cap of 50.

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
- in this case the convergence count resets (the 2-consecutive-round condition starts over)
- changing the judgment is free, but it becomes a cause of longer rounds

### human-proponent absent mode

- a mode where the caller skips collecting human responses
- the rounds proceed with only the initial evidence-list
- the no-response judgment triggers immediately, so the tendency toward convergence-reject is strong
- in this mode, convergence-judge-deadlock condition 3 (no response for 3 consecutive rounds) does not apply. Instead, the human-response-based convergence conditions are entirely lifted

### Agent replacement

- on a 3-round cumulative failure of the sycophancy audit, replace the agent
- the replaced agent is spawned anew. The previous agent's utterance record is kept, but its final-verdict is voided
- the convergence count resets after the replacement point
- the replacement itself is not a reason to extend the rounds

### Immediate convergence at round 5

- if the convergence conditions are met at the end of round 5, converge immediately
- even in this case rounds 1 through 4 cannot be skipped. At verification-complexity-level-2 and above, "only 3 rounds for a simple claim" is prohibited
- at verification-complexity-level-2 and above, the minimum of 5 is guaranteed absolutely. A verification-complexity-level-1 claim is not a target of adversarial rounds

### Request to exceed the round cap of 50

- the case where the caller requests raising the cap further (for example, 100 rounds)
- not allowed. 50 is the balance point of cost and convergence likelihood
- if it does not converge even at 50 rounds, that problem cannot be solved with rounds. The meta-judge or a human must solve it

### Simultaneous claim verification

- when verifying multiple claims at once, each claim gets independent rounds
- the agents may be shared, but the rounds-log is separated per claim
- cross-attacks between claims are not allowed (to prevent confusion)
