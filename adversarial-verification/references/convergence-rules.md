# Convergence Rules

After every complete independent attack cycle and its audit, the coordinating assistant applies the configured consensus rule and records one convergence state. The default is unanimity; majority or weighted voting requires explicit caller/domain configuration. An unresolved objection does not disappear because time or rounds elapsed.

## State selection

| State | Conditions | Caller action |
| --- | --- | --- |
| accept | The first cycle covered every required distinct axis; all currently affected axes completed review and retained unaffected findings remain valid; consensus passes; audit has no violations; no decision-relevant uncertainty or attainable relevant delta remains | Return the surviving claim and assumptions |
| reject | A decisive attack remains unresolved without new evidence and consensus supports rejection; or all evidence was eliminated; or the proponent withdraws the claim | Return rejection reasons and withdrawn claims |
| judge-deadlock | The 50-round cap is reached without accept/reject; or verdicts conflict and no identified attack/evidence check can change them | Invoke the meta-judge and retain the claim as unresolved; ask the user only for missing authorization or a user-owned choice |
| partial | A new attack or evidence changes the surviving claim, assumptions or guarantee boundary, and the named uncertainty can change the next decision | Run the next bounded round |

A first complete cycle may converge immediately and must include the full level-2 or level-3 role composition. Later cycles use the round protocol's affected-axis selection; a missing first-cycle submission cannot be treated as retained evidence. Repeating an attack or receiving no response is not independently rejection. The coordinating assistant evaluates the unresolved decisive attack under the consensus rule. It must not spend no-op rounds waiting for agreement.

## Determine whether an attack is new

The coordinating assistant compares target-claim-id, attack-type and the core argument: which claim part is wrong, why it is wrong and what evidence could resolve the challenge.

If those elements are the same, paraphrasing does not create a new attack. A different target, error mechanism, decision-relevant distinction or required resolving evidence creates a new attack. A merely different wording or perspective on the same core argument does not.

The coordinating assistant first compares target/type deterministically, then compares the arguments. If the semantic comparison remains ambiguous, it may obtain one auxiliary model judgment and must record that judgment. If ambiguity remains, it treats the attack as new. This conservative classification does not remove the finite cap or permit rounds with no attainable decision change.

For example, two wordings of the same revenue/source discrepancy are the same attack. A challenge that the revenue computation lacks a pre-tax/post-tax distinction is different because it identifies a different defect.

## Evidence and verdict changes

An absent proponent response, repeated evidence or an unrelated response supplies no new evidence. In declared human-absent mode, the coordinating assistant skips response collection and judges the initial evidence after the complete cycle. Human absence alone does not reject the claim or require waiting for a response-count threshold.

An agent may change its verdict. The change justifies another round only when it reopens a named uncertainty that another round can resolve. A changed surviving claim or assumption receives affected review; unchanged unrelated axes do not need repetition.

## Replacement and limits

After cumulative audit failure over three rounds, the coordinating assistant replaces the violating agent. It retains the old utterances, voids that agent's verdict and reruns the replaced axis against current evidence. Replacement alone does not extend unaffected axes.

The 50-round safety cap cannot be increased. At the cap, or earlier no-delta disagreement, the coordinating assistant invokes the meta-judge defined in ../SKILL.md. The meta-judge reports the dispute, unresolved evidence and any missing user-owned decision; it does not decide the claim. The coordinating assistant follows that skill's conditional handoff, preserves the unmet convergence requirement and continues independent authorized work. Factual disagreement alone does not require a human accept/reject decision.

For simultaneous claims, the coordinating assistant keeps independent rounds and logs per claim. Agents may be reused across claims, but cross-claim attacks are excluded.
