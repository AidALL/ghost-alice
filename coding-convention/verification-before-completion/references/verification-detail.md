# Verification detail

Worked verification patterns for `verification-before-completion`.

## Verification Patterns

Tests:

```text
Run the test command, read the result, then claim only the observed result.
```

Regression tests:

```text
Write the test -> run and observe pass -> revert or disable the fix -> observe fail -> restore fix -> observe pass.
```

Builds:

```text
Run the build command and read exit code 0 before claiming build success.
```

Requirements:

```text
Re-read the user intent and contract -> write acceptance criteria -> verify each criterion -> report missing criteria or verified completion.
```

Agent delegation:

```text
Read the agent report -> inspect current accessible behavior when needed -> apply the uncertainty gate -> run only a decision-relevant check -> report the supported state.
```

## Useful outcomes and bounded repair

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

A known-contract executable correction still uses its owning code-TDD workflow. A failed diagnostic or unavailable infrastructure does not itself count as a corrective repair round.
