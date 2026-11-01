# Validation at Distinct Boundaries

Use this pattern after tracing a bad input when multiple actual entry paths can bypass a single check. Each boundary enforces its own invariant; consumers reuse a shared policy decision rather than independently redefining it.

## Select the checks

| Boundary | Responsibility |
| --- | --- |
| Public entry | Reject malformed or absent required input before admitting the operation. |
| Business operation | Verify the input is meaningful for that specific action. |
| Execution environment | Prevent an operation whose target violates the environment's accepted boundary. |
| Diagnostics | Record safe context to explain a failure; logging does not decide policy. |

The response model maps the observed data flow and identifies which caller can bypass each earlier check. Add a later check only for a distinct invariant or bypass. The presence of four rows does not require four implementations.

## Example

A test reads an empty project directory before fixture setup and causes Git initialization in the source checkout. Correct the fixture. Validate required directory input at the entry point. If another caller can invoke workspace initialization directly, enforce its directory precondition there. If tests require all Git operations inside their sandbox, enforce that environment boundary at the Git operation.

For filesystem containment, compare resolved path components under the platform's path contract; a textual prefix alone does not establish directory containment. Use diagnostics only when the remaining causal uncertainty needs them.

## Verification and failure modes

Exercise the original failure and the identified bypass paths. A check that cannot be reached by an alternate caller need not be duplicated merely for reassurance. If callers disagree about the permitted operation, correct the shared policy implementation and its callers rather than adding a competing policy decision. Do not claim that logging prevents the failure.
