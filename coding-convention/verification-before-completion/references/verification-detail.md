# Verification Patterns

Use these patterns after applying the main skill's evidence selection and reuse decision.

| Claim | Relevant observation |
| --- | --- |
| Target tests passed | Actual output and status from those tests, with target and failures identified. |
| Regression detects the original defect | Required RED/GREEN evidence from test-driven-development; retained original results remain usable. |
| Build succeeded | Actual build result for the claimed tree and environment. |
| Requirements satisfied | A claim-evidence mapping covering each accepted criterion. |
| Delegated task completed | Inspected target plus criterion-relevant evidence, not the agent's status alone. |
| Source guidance corrected | Changed instruction and its affected source-contract validation. |
| Host behavior corrected | Observation of the relevant host execution, beyond source or installation evidence. |

Do not reverse a valid fix solely to recreate existing RED evidence. If the required original failure proof is unavailable, follow test-driven-development’s applicability and RED-evidence instructions within the authorized target, and report which required observation is missing.

The main skill owns [useful-check selection](../SKILL.md#evidence-selection-and-stop-gate).
