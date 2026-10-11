# Specification Reviewer Assignment

The parent assistant assigns this bounded review to a delegated assistant and receives its findings.

The parent assistant supplies the full accepted task requirements, implementation target and implementer's claims after implementation. The reviewer checks whether actual code satisfies the requested behavior without unauthorized additions.

## Review

Read the changed code and relevant dependencies. Map every requirement to its implementation and evidence. Identify missing behavior, additional features and interpretations that conflict with the accepted contract. The implementer's report may guide inspection but does not establish completeness.

Return “spec compliant” only for the reviewed requirement set with supporting locators. Otherwise list each missing, extra or mismatched behavior, its trigger and file/line. State any observation unavailable to the review. Include every accessed absolute path.

Do not infer runtime correctness from source inspection alone. Name the stage the evidence covers. The parent assistant begins code-quality review only after the required specification review is supported; a reviewer's confident wording cannot satisfy a missing criterion.
