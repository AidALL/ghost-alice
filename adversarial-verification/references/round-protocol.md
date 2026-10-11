# Round Protocol

The coordinating assistant coordinates independent attacks, proponent evidence, its own meaning review of each utterance and convergence. Fixed review criteria do not make that semantic judgment a deterministic code operation. Normal rounds have no mediator. The coordinating assistant records every phase so that a later meta-judge or human can inspect the reasoning.

## Round sequence

1. Phase A: the first cycle includes every required distinct axis. In a later cycle, the coordinating assistant assigns the axes affected by changed claims, assumptions, evidence or eligible prior-round challenges and retains unaffected valid findings. Each assigned agent independently submits an own-axis attack or reasoned no-attack declaration, plus any prior-round meta-attack or reasoned no-meta-attack declaration. Current-cycle submissions are unavailable to peers.
2. Phase B: the proponent, or caller in that role, reads the attacks and may submit new evidence, resolve a mistaken premise or withdraw part/all of the claim. The coordinating assistant records absent, repeated or irrelevant responses as no new evidence. In declared human-absent mode, it skips collection and uses the initial evidence.
3. Phase C: the coordinating assistant audits every utterance under the rules below. It checks the claim-card/evidence-list against the source of truth only when new evidence, changed schema or an observed mismatch can affect the verdict. A mismatch becomes factual attack material, not a compromise judgment. Deleted source evidence or material schema change requires escalation.
4. Phase D: the coordinating assistant records the cycle and applies convergence-rules.md immediately. It continues only when another round can change an identified decision.

Parallel dispatch preserves independent submissions. If parallel dispatch or sufficient capacity is unavailable, use sequential isolated assignments; each receives only prior-round utterances and an explicit instruction not to reference current-round peers. In asynchronous collection, use a bounded wait within the task's resources; timeout records no response, never a favorable verdict.

### Dispatch and collect through the host

The coordinating assistant reads the current host's actual delegation schema. On Codex it loads `using-coding-convention` from the current skill catalog, then its `references/codex-tools.md` section `Agent dispatch`; the [source navigation link](../../coding-convention/using-coding-convention/references/codex-tools.md#agent-dispatch) is not an installed path recipe. It submits a self-contained prompt containing the assigned axis, current claim-card, original source locators/content, schema context, only completed prior-round submissions, allowed actions/resource limits and the submission format below. Require every accessed absolute path and evidence limit in the result. A role label in this prompt is a task assignment, not an invented tool parameter or higher-priority instruction.

Retain the dispatch tool's returned handle with its claim ID, round and axis. Use that same API's documented wait/result operation to collect the corresponding submission. The coordinating assistant places results in the round log, checks required fields, then performs Phase C; a spawn acknowledgment or agent status is not the review. If no supported delegation tool exists, report the required independent review as unavailable rather than inventing agents or replacing it with self-agreement.

## Agent submission

```text
agent-id: <assigned agent>
round: <round number>
axis-attack:
  - target-claim-id: <claim or subclaim>
    attack-type: <logical-gap | contradiction | hidden-premise | term-equivocation | ...>
    attack-content: <concrete defect and resolution condition>
    referenced-evidence: <evidence ID when applicable>
  OR
  declared: no attack point
  reason: <why no semantically new own-axis attack remains>
meta-attack:
  - target-agent: <agent>
    target-round: <prior round>
    attack-content: <specific defect in that prior submission>
  OR
  declared: no meta attack
final-verdict-this-round: accept | reject | hold
```

Every submission includes axis-attack and final-verdict. Round 1 may omit meta-attack because no prior-round target exists. A bare verdict or empty axis response is invalid. Meta-attack cannot hide an ordinary off-axis claim attack.

## Attack, concession and response rules

An attack identifies a concrete claim, source, clause or reasoning step, states the defect and explains what could resolve it. General suspicion or a demand for unspecified checking is invalid.

A concession identifies the assigned review assistant's own defeated attack/evidence, the opposing evidence/response and the resolution path, then states the remaining claim boundary:

```text
agent-id: <agent>
round: <round>
concession:
  target-own-attack-id: <prior attack>
  negated-by: <response or counterattack>
  negation-path: <one or two sentences>
  remaining-claim-part: <surviving part>
```

The assigned review assistant may concede only when the opposing material concretely defeats its attack and it can state both the path and surviving scope. Feeling persuaded or pressure to finish does not support concession. A concession concerns an old attack; no attack point concerns absence of a new one. The assigned review assistant records them separately when both apply.

The proponent may add a file/link/logical path, explain that an attacked premise is absent from the claim, or withdraw a part while retaining a narrower claim. Repetition, summarizing without answering and changing topic add no evidence.

## Mandatory utterance audit

In Phase C of every round, the coordinating assistant checks for:

- unsupported agreement or concession without its required resolution path;
- vague attacks or bare no-attack declarations;
- compromise/mediation instead of a factual or logical judgment;
- personal attacks or unsupported motive claims;
- own-axis violations or a generic axis attack disguised as meta-attack.

The coordinating assistant invalidates a violating utterance and asks that agent to resubmit; the round is extended once. Cumulative audit failure over three rounds requires agent replacement under convergence-rules.md. Every submission must pass before the cycle can close as accepted.

The coordinating assistant performs this semantic review without a separate routine audit agent. For an ambiguous concrete-versus-general judgment, it may dispatch one bounded auxiliary judgment containing the disputed utterance, required criterion and source context; retain the returned reasoning in the log. Matching IDs and field presence are mechanical checks, not substitutes for that judgment.

## Log and retention

```text
rounds-log:
  claim-id: <target>
  rounds:
    - round: 1
      phase-a:
        - <independent agent submission>
      phase-b:
        human-response: <response or none>
      phase-c:
        audit-result: pass | fail
        audit-violations: <violations>
  summary:
    total-rounds: <count>
    convergence: accept | reject | judge-deadlock
    retracted-claims: <withdrawn claims>
```

The coordinating assistant saves the full log in the existing authorized evidence location and returns its exact locator with the claim ID, round count, convergence and up to ten issues. The meta-judge/human escalation receives that log and the current claim-card. If no file storage is authorized, retain the log in task context and report the durable-retention limit; do not create a new storage boundary. For a stored log, retain it for at least 30 days after the result is reflected in the deliverable, within applicable retention authority; do not export sensitive evidence beyond the authorized workspace.
