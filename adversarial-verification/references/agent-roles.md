# Agent Roles

The coordinating assistant assigns distinct claim-attack axes. Each assigned review assistant attacks the claim only on its assigned axis; an attack on another agent's prior-round verdict may use any axis. The coordinating assistant excludes out-of-axis claim attacks from the convergence decision. Each assigned review assistant must give a concrete attack or a reasoned no-attack declaration.

## Fixed roles

| Role | Claim-attack target | Valid example | Excluded claim attack |
| --- | --- | --- | --- |
| agent-C-internal-logic | Premises, causal links, implications, hidden assumptions and conflicting definitions inside the claim | “Clause A makes age a necessary condition; the claim treats it as sufficient for eligibility” | Demanding a founding-date source or asserting that a source is weak |
| agent-C-external-fact | Agreement with source documents, schema-context and external facts | “The cited source records 148; the claim reports 155” | Internal logical criticism or a value judgment |
| agent-C-internal-fact | Agreement of numbers, dates and conditions within the same deliverable | “One section states 14 employees and another 10 full-time staff; the relationship is unstated” | Comparing external sources or attacking the logical form |

The internal-logic agent identifies the missing implication or precise contradiction. The external-fact agent identifies the conflicting source field, absent citation, misread original text or inaccessible evidence. The internal-fact agent identifies both locations inside the deliverable. A generic request for more evidence is not an attack without a concrete conflict.

## Extended roles

At level 3, the coordinating assistant adds two roles from this pool based on claim-type and domain-axes. The prior-art role is required for patent and paper claims.

| Role | Claim-attack target | Valid example | Excluded claim attack |
| --- | --- | --- | --- |
| agent-C-external-logic | Conflict with the interpretive logic of law, standards or documented conventions | “The interpretation conflicts with the standard's definition” | Simple factual or internal-logic criticism |
| agent-C-edge-case | Supported boundary conditions or counterexamples | “Under condition X, the claimed bound no longer holds” | An imagined scenario with no basis for possible occurrence |
| agent-C-prior-art | Conflict with published prior claims, papers, patents or products | “Published patent P contradicts the claimed novelty” | “It probably exists” without a source or irrelevant cross-domain similarity |
| agent-C-incentive | Evidence of stakeholder bias in evidence selection or framing | “The evidence list omits the cited unfavorable result” | Personal attacks or unsupported motive speculation |

## Meta-attacks

An agent may challenge a specific prior-round verdict, omission, evidence judgment or counterexample outside its assigned claim axis. It names the assigned review assistant, round, statement and defect.

Valid examples include identifying an unexamined premise behind a no-attack declaration, showing that an accepted citation is only a secondary source when primary evidence is needed, or showing that a counterexample lacks a supported possibility.

“Agent X is wrong,” “X is taking sides,” and unsupported agreement are invalid. A meta-attack is not a vehicle for placing an ordinary off-axis claim attack into the record. The coordinating assistant audits the distinction through round-protocol.md.

## Assignment by complexity

| Complexity | Agents | Composition |
| --- | --- | --- |
| level 1 | zero | Entry evidence precheck only |
| level 2 | three | Fixed internal-logic, external-fact, internal-fact |
| level 3 | five | Fixed three plus two distinct extended roles |

Simple operational documents normally use level 1. Regulation, contract or policy interpretation normally uses level 2, with level 3 for high-risk claims. Patents, papers, investor-relations and legal documents normally use level 3. Technical documents normally use level 1, increasing for numeric, compatibility or security claims. The coordinating assistant supplies the actual complexity for the admitted claim; these defaults do not waive its evidence burden.
