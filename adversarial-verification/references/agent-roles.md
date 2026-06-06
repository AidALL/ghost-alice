# Agent Role Details

This document defines the attack axis, allowed attack patterns, forbidden attack patterns, and examples for the seven agent roles used in the adversarial-verification skill.

## Table of Contents

- [Assignment Principles](#assignment-principles)
- [Fixed Three (verification-complexity-level-2)](#fixed-three-verification-complexity-level-2)
- [Extended Pool of Four (two chosen dynamically at verification-complexity-level-3)](#extended-pool-of-four-two-chosen-dynamically-at-verification-complexity-level-3)
- [Meta-Attack Rules](#meta-attack-rules)
- [Assignment Table by Complexity](#assignment-table-by-complexity)

---

## Assignment Principles

- One agent per axis. No axis overlap. Overlap collapses diversification and amplifies sycophancy.
- Each agent attacks a claim only on its own axis (the role is fixed).
- However, an agent may attack another agent's verdict on any axis (the meta-attack is not fixed).
- If an agent finds no attack point on its own axis, it must declare "no attack point". Silence is forbidden. Silence cannot be used in the convergence verdict.
- An agent must attack using only the evidence of its own axis. If it intrudes on another axis, that statement is void.

---

## Fixed Three (verification-complexity-level-2)

### agent-C-internal-logic

○ Dedicated axis
- Internal logical consistency. Conflicts among the premises, causation, implications, and implicit assumptions inside a claim.

○ Allowed attack patterns
- "There is a causal break between premise P and conclusion Q of the claim. The intermediate step R is not stated."
- "An implicit assumption A is hidden inside the claim. If A is false, the entire claim collapses."
- "Sub-elements X and Y of the claim cannot both be true at once. This is a contradiction."
- "Term T used in the claim appears twice with two different meanings. This is an equivocation error."

○ Forbidden attack patterns
- Demanding external factual evidence (handled by the external-fact agent).
- "The source is weak." (intruding on the fact-handling axis)
- "It feels intuitively off." (no specificity)

○ Examples
- Attack target: "We meet the under-three-years-since-founding condition, so we are eligible to apply."
- Allowed: "The claim treats under three years since founding as a sufficient condition, but clause A of the notice makes it a necessary condition. This confuses sufficient and necessary."
- Forbidden: "Wasn't the company founded in 2023? This needs checking." (fact checking belongs to another axis)

### agent-C-external-fact

○ Dedicated axis
- External factual evidence. Comparing the claim against facts in the schema-context, evidence-list, and external reference materials.

○ Allowed attack patterns
- "The figure N in the claim does not match the value M of the corresponding field in the SSOT."
- "The content does not exist in document D that the claim cites."
- "The claim's interpretation of the statutory text does not match the original text."
- "Evidence E in the evidence-list is inaccessible or expired."

○ Forbidden attack patterns
- Attacking the internal logic of the claim (handled by internal-logic).
- "This assertion is offensive." (a value judgment)
- "I want more evidence." (forbidden without a concrete factual conflict)

○ Examples
- Attack target: "Figure A in the report is 155."
- Allowed: "The approved source record states figure A as 148. A discrepancy of 7."
- Forbidden: "155 is too specific." (no factual conflict)

### agent-C-internal-fact

○ Dedicated axis
- Internal factual consistency. Agreement of figures, dates, and conditions with the other claims inside the same document or the same deliverable.

○ Allowed attack patterns
- "The figure in the claim does not match the figure in section S of the same document."
- "The date in the claim does not match the schedule in the same document."
- "The condition in the claim does not match the premise in the same document."

○ Forbidden attack patterns
- Comparing against external materials (handled by external-fact).
- Attacking the logical structure (handled by internal-logic).

○ Examples
- Attack target: "Our company has 14 employees, and 12 of them are R&D staff."
- If another section of the same document says "10 full-time employees", then
- Allowed: "The relationship between the 14 employees and the 10 full-time employees is not stated. If there are 4 non-regular employees, the claim of 12 R&D staff needs re-verification."

---

## Extended Pool of Four (two chosen dynamically at verification-complexity-level-3)

At verification-complexity-level-3, in addition to the fixed three, choose two dynamically from the pool below according to the claim's characteristics. The selection criteria are specified by the caller based on claim-type and domain-axes, or, when unspecified, chosen automatically based on claim-type.

### agent-C-external-logic

○ Dedicated axis
- External logical consistency. Conflicts between the claim's interpretive logic and the interpretive logic of statutory text, technical standards, or industry conventions.

○ Allowed attack patterns
- "The claim's interpretation of the statutory text conflicts with case law."
- "The claim's interpretation of the technical standard conflicts with the definition in the standard document."
- "The claim's assertion about industry convention is stated without a verifiable source."

○ Forbidden attack patterns
- Simple fact checking (handled by external-fact).
- Attacking internal logic (handled by internal-logic).

### agent-C-edge-case

○ Dedicated axis
- Counterexample scenarios. Exploring boundary cases of the form "if this condition holds, the claim breaks".

○ Allowed attack patterns
- "If condition X occurs, the figure in the claim does not hold."
- "After time T, the evidence for the claim is no longer valid."
- "Outside the target range R, the claim is void."

○ Forbidden attack patterns
- General suspicion ("something feels off").
- Imagined scenarios (no basis for the possibility of occurrence).

### agent-C-prior-art

○ Dedicated axis
- Conflict with prior assertions and public materials. Required in the patent and paper domains.

○ Allowed attack patterns
- "Published prior patent P conflicts with the claim's novelty assertion."
- "The results of published paper A rebut the claim's contribution assertion."
- "Existing product X has already commercialized the claim's technology."

○ Forbidden attack patterns
- A sourceless "it probably already exists".
- Asserting similarity outside the domain.

### agent-C-incentive

○ Dedicated axis
- Interests and bias. "Whose interests does this assertion represent, and did those interests influence the selection of evidence?"

○ Allowed attack patterns
- "The claim's evidence is selected only in a direction favorable to a particular stakeholder."
- "Unfavorable evidence is excluded from the evidence list."
- "The assertion's framing is biased to fit the objective."

○ Forbidden attack patterns
- Personal attacks.
- Guessing motives (without evidence).

---

## Meta-Attack Rules

Apart from attacks on its own axis, each agent may attack the statements of another agent themselves. This is the core of the free-for-all.

○ Allowed meta-attacks
- "agent-C-internal-logic declared 'no attack point' in round 3, but the basis for that declaration did not examine premise P of the claim. An examination omission."
- "agent-C-external-fact judged in round 2 that evidence E was sufficient, but E is not a primary source, it is a secondary citation. It needs re-confirmation against a primary source."
- "The counterexample agent-C-edge-case presented in round 4 has no basis for an actual possibility of occurrence."

○ Forbidden meta-attacks
- "agent-X is wrong." (no specificity)
- "agent-X's verdict looks like it is taking someone's side." (a bias guess)
- "I agree with agent-X." (this is concession, not a meta-attack, and that concession is forbidden too)

○ The significance of meta-attacks
- With meta-attacks present, no agent can easily declare "no attack point". The declaration itself becomes a target of another agent's attack.
- Sycophancy is blocked structurally. Even if one agrees with another agent, that agreement is attacked again.

---

## Assignment Table by Complexity

| Complexity | Number of agents | Role composition |
|--------|-------------|-----------|
| verification-complexity-level-1 | 0 (no round entry) | Immediate verdict at Step 0/1 |
| verification-complexity-level-2 | Fixed three | internal-logic, external-fact, internal-fact |
| verification-complexity-level-3 | Five | Fixed three + two chosen dynamically from the extended pool of four |

○ Complexity determination (based on the verification-complexity passed by the caller)
- verification-complexity-level-1: Self-evident facts, simple comparison. No adversarial round needed.
- verification-complexity-level-2: Claims involving external statutory text, technical standards, or a possible counterexample.
- verification-complexity-level-3: Claims where prior assertions and interests matter, such as patents, papers, IR, and policy.

○ Recommended complexity by domain
- Simple operational documents: verification-complexity-level-1 by default (no round entry, evidence pre-check only).
- Documents involving interpretation of regulations, contracts, or policy: verification-complexity-level-2 by default, with verification-complexity-level-3 for high-risk claims.
- Patents, papers, IR, and legal documents: verification-complexity-level-3 by default.
- Technical documents: verification-complexity-level-1 by default, with verification-complexity-level-2 or above for figure, compatibility, and security claims.
