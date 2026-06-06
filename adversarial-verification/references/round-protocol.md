# Round Protocol

This document defines the round execution protocol of the adversarial-verification skill, the utterance pattern constraints, the list of forbidden utterances, and the sycophancy audit procedure.

## Table of Contents

- [Round Basic Structure](#round-basic-structure)
- [Agent Utterance Format](#agent-utterance-format)
- [Allowed Utterance Patterns](#allowed-utterance-patterns)
- [Forbidden Utterance Patterns](#forbidden-utterance-patterns)
- [Concession Utterance Rules](#concession-utterance-rules)
- [human-proponent Response Rules](#human-proponent-response-rules)
- [sycophancy Audit](#sycophancy-audit)
- [rounds-log Format](#rounds-log-format)

---

## Round Basic Structure

One round consists of the following sequence (there is no physical ordering, but a recording order does exist).

- Phase A is Agent parallel utterance.
  - Every agent independently writes its own-axis attack plus a meta attack
  - Submitted simultaneously, mutual reference is forbidden (referenced in the next round)
- Phase B is Human-proponent response.
  - Read every attack from Phase A and submit response evidence
  - If there is no response or it merely repeats existing evidence, reject accumulates
- Phase C is Checkpoint (every 5 rounds).
  - Every 5 rounds (round 5, 10, 15, ...), re-check the claim-card and evidence-list against the SSOT
  - When a mismatch is found, provide it as attack material to the next-round agents. The checkpoint does not judge
  - Switch to verdict=escalate only when the evidence has been deleted from the SSOT or the schema has changed
  - The checkpoint does not mediate. A judgment such as "both sides have a point" is forbidden. Perform fact-checking only
  - In rounds that are not a multiple of 5, skip this phase
- Phase D is Round close record.
  - Record the Phase A, Phase B, and Phase C utterances in the rounds-log
  - Check the convergence condition (only after round >= 5)

○ parallel vs sequential
- Dispatching Phase A as parallel subagents forces independent utterance with no mutual reference. This is ideal
- In an environment where parallel dispatch is not possible, run sequentially, but state in each agent prompt "do not reference the other agents' utterances from this round. Reference only up to the previous round"

○ round interval
- Secure time between rounds to collect the human-proponent response. In asynchronous mode, set a response timeout
- Default behavior on response timeout: continue the round, and record that there was no response for that claim

---

## Agent Utterance Format

Each agent utters in the following structure every round.

```
agent-id: <e.g., agent-C-internal-logic>
round: <N>
axis-attack:
  - target-claim-id: <claim ID or sub-element>
    attack-type: <logical-gap | contradiction | hidden-premise | term-equivocation | ...>
    attack-content: "one concrete attack sentence"
    referenced-evidence: <evidence ID, if applicable>
  OR
  declared: "no attack point"
  reason: "the reason no new attack point was found in this round"
meta-attack:
  - target-agent: <other agent id>
    target-round: <previous round N>
    attack-content: "one meta attack sentence"
  OR
  declared: "no meta attack"
final-verdict-this-round: <accept | reject | hold>
```

○ required fields
- axis-attack: cannot be empty. Either an attack or "no attack point"
- meta-attack: required from round 2. Round 1 has no meta attack target, so it can be omitted
- final-verdict-this-round: submitted every round. Used to record the mid-round judgment

○ forbidden
- writing another agent's axis attack in axis-attack
- writing a general axis attack in meta-attack
- submitting final-verdict without an utterance

---

## Allowed Utterance Patterns

○ attack utterance
- Points at a concrete claim, evidence, document, or clause
- The logical structure of the attack is stated
- Presents together what is needed for the attack to be resolved

○ concession utterance (allowed format only)
- "my evidence X is negated by the counterpart's evidence Y. Negation path: ~~~"
- "my attack Z is resolved by response R. Resolution condition: ~~~"
- A concession must state the negation or resolution path

○ "no attack point" declaration
- "I could not find a new attack point beyond the attacks up to the previous round on my own axis"
- A bare "none" is forbidden. The reason is required

---

## Forbidden Utterance Patterns

The following utterances are invalidated at the audit stage. The agent that uttered them is asked to re-utter for that round.

○ agreement without evidence
- "I think that is correct"
- "that seems right"
- "good point"
- "I agree" (without a path)

○ generalities
- "it is a bit odd"
- "something is lacking"
- "it needs more checking" (without stating what to check)

○ compromise or mediation
- "both sides have a point"
- "let us settle at a reasonable middle"
- "this is good enough"

○ personal attack or bias
- "agent-X is biased" (without evidence)
- "the human-proponent is emotional"

○ out of domain
- an attack on an axis that is not one's own
- a judgment beyond one's own role

---

## Concession Utterance Rules

A concession is the most dangerous point of this skill. It is where sycophancy leaks the most. Concession is allowed, but the format is strictly constrained.

○ concession allowed conditions (AND)
1. One's own attack or one's own evidence has been "concretely" negated by the counterpart
2. The negation path can be stated in 1 to 2 sentences
3. What part of the claim remains after the negation can be stated

○ concession forbidden conditions (OR)
- a sense of "somehow being persuaded"
- the negation path cannot be stated
- pressure to finish quickly because the round count has dropped

○ concession record format
```
agent-id: <e.g., agent-C-internal-logic>
round: <N>
concession:
  target-own-attack-id: <previous round attack ID>
  negated-by: <response or counter-attack ID>
  negation-path: "1 to 2 sentence negation path"
  remaining-claim-part: "the claim part that remains after the concession"
```

○ a concession is different from no attack point
- concession: acknowledging that one's own previous attack was negated
- no attack point: failing to find new attack material
- both are possible, but record them separately

---

## human-proponent Response Rules

The response from the human side (or the evidence the caller submitted in the human role) also has format constraints. Defending unconditionally also runs counter to the skill's intent.

○ allowed response format
- submit new evidence: add an evidence file, link, or logical path that did not exist before
- resolution explanation: show that the attack started from a specific premise, and explain that the premise is not in the claim
- partial withdrawal: "I concede this part. I keep only this part"

○ forbidden responses
- repeating existing evidence without adding any ("as I already said")
- only summarizing the attack without responding
- switching to a different topic

○ no-response handling
- when no response accumulates for 2 consecutive rounds, that claim starts accumulating convergence-reject
- when the caller has declared human-absent mode (automatic verification): proceed with the round on the initial evidence only, and skip the response stage

---

## sycophancy Audit

Periodically audit whether the round protocol is working correctly.

○ audit timing
- automatically run in Phase C of every round
- before the round closes, all utterances of that round must pass the audit

○ audit items
- detection of forbidden utterance patterns
- whether a concession utterance kept the format constraints
- whether a "no attack point" declaration has a reason
- whether a meta attack is disguised as a general axis attack
- whether each agent stayed within its own axis

○ behavior on audit failure
- a violating utterance is invalidated
- the agent is asked to re-utter (the round is extended once)
- when audit failures accumulate over 3 rounds, replace that agent (spawn a new subagent)

○ audit executor
- the audit runs with this skill's own logic
- a separate audit agent is not needed (a deterministic check)
- however, when the "generality vs concrete" boundary judgment is ambiguous, one auxiliary LLM judgment is allowed

---

## rounds-log Format

The record of every round accumulates in the rounds-log. The log is used for traceability, audit, and meta-judge input.

○ log structure
```
rounds-log:
  claim-id: <verification target>
  rounds:
    - round: 1
      phase-a:
        - <agent utterance>
        - <agent utterance>
        - ...
      phase-b:
        human-response: <response content or "none">
      phase-c:
        audit-result: pass | fail
        audit-violations: [...]
    - round: 2
      ...
  summary:
    total-rounds: N
    convergence: accept | reject | judge-deadlock
    retracted-claims: [...]
```

○ log splitting
- save the full log to a file. Return only the summary to the caller
- summary format: claim-id + total-rounds + convergence + the issue list (up to 10)
- the caller references the full log file path on meta-judge or human escalation

○ log retention
- minimum retention period: 30 days after the skill call result is reflected in the deliverable
- because caller-provided evidence may contain sensitive information, do not export it outside the authorized workspace
