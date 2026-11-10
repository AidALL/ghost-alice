# Code Quality Review Assignment

The parent (the session or agent that started the delegate) assigns this bounded review to a delegate and receives its findings.

The parent dispatches this review only after specification compliance is supported. The parent fills requesting-code-review's [code review assignment](../../requesting-code-review/references/code-reviewer.md) with the task from the plan that the response model saved under writing-plans, exact base/head or working-tree target, implementation summary and evidence.

Inspect the changed responsibilities and interfaces, testability, the file structure that the plan maps and maintainability consequences introduced by this change. Do not flag pre-existing file size alone or require unrelated restructuring.

Return severity-calibrated findings, checked-stage assessment, unresolved limits and accessed absolute paths. The parent evaluates findings against the requirements that the human user accepted and the plan or the boundary contract before requesting corrections. The boundary contract's acceptance criteria and locked decisions carry source labels (`[source: ...]`); its prohibited paths, layers and actions carry no per-item source label. The delegate does not expand implementation authority.
