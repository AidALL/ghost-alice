# Specification Review Assignment

The parent (the session or agent that started the delegate) assigns this bounded review to a delegate and receives its findings.

After implementation, the parent prepares the requirements that the human user accepted, as the parent's assignment states them, and the exact review target under requesting-code-review's [assignment inputs](../../requesting-code-review/references/code-reviewer.md#assignment-inputs), including original-source access and the human user's corrections to those requirements separately from the parent's interpretation. The implementing delegate's claims are context for inspection. The delegate checks whether the reviewed artifacts satisfy the behavior that the human user requested without additions the human user did not authorize.

## Review

Read the changed artifacts and relevant dependencies at the supplied target, using the shared [evidence guidance](../../requesting-code-review/references/code-reviewer.md#evidence-by-artifact) for code, instructions, procedures or a mixed change. Map every requirement to its implementation and evidence. Identify missing behavior, additional features and interpretations that conflict with the behavior contract or original constraints that the human user accepted. The implementing delegate's report may guide inspection but does not establish completeness.

Return “spec compliant” only for the reviewed requirement set with supporting locators. Otherwise list each missing, extra or mismatched behavior, its trigger and file/line. State any observation unavailable to the review. Include every accessed absolute path.

Do not infer runtime correctness from source inspection alone. Name the stage the evidence covers. The parent begins code-quality review only after the required specification review is supported; confident wording in the review cannot satisfy a missing acceptance criterion.
