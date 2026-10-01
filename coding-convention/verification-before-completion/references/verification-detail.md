# Verification detail

Detail moved out of `verification-before-completion/SKILL.md` for progressive disclosure: worked verification patterns.

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
