# Specification Review Assignment

The parent assigns this bounded review to a delegate and receives its findings.

The parent supplies the full accepted task requirements, implementation target and the implementing delegate's claims after implementation. The delegate checks whether actual code satisfies the requested behavior without unauthorized additions.

## Review

Read the changed code and relevant dependencies. Map every requirement to its implementation and evidence. Identify missing behavior, additional features and interpretations that conflict with the accepted contract. The implementing delegate's report may guide inspection but does not establish completeness.

Return “spec compliant” only for the reviewed requirement set with supporting locators. Otherwise list each missing, extra or mismatched behavior, its trigger and file/line. State any observation unavailable to the review. Include every accessed absolute path.

Do not infer runtime correctness from source inspection alone. Name the stage the evidence covers. The parent begins code-quality review only after the required specification review is supported; confident wording in the review cannot satisfy a missing criterion.
