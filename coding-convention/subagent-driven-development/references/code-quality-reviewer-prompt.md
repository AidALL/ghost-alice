# Code Quality Reviewer Assignment

The parent assistant assigns this bounded review to a delegated assistant and receives its findings.

The parent assistant dispatches this reviewer only after specification compliance is supported. Use requesting-code-review's [reviewer assignment](../../requesting-code-review/references/code-reviewer.md) with the accepted task, exact base/head or working-tree target, implementation summary and evidence.

Inspect the changed responsibilities and interfaces, testability, accepted file structure and maintainability consequences introduced by this change. Do not flag pre-existing file size alone or require unrelated restructuring.

Return supported strengths, severity-calibrated findings, checked-stage assessment, unresolved limits and accessed absolute paths. The parent assistant evaluates findings against the accepted contract before requesting corrections; the reviewer does not expand implementation authority.
