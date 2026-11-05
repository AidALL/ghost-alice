# Implementation Assignment

The delegate implements the bounded task below and returns its evidence to the parent that assigned it.

The parent supplies a current task brief, exact working directory, allowed files, prohibited side effects, resources and result format, with accessible original-source locators for the accepted task, constraints, known corrections and dependencies. The locators identify the applicable revision and passage, field or record when needed to distinguish the source.

For retrieval and later handoffs, the delegate reads `references/context-selection.md` in the installed task-router folder. The delegate retrieves the passages needed for its current decision rather than receiving accumulated source bodies. If the delegate cannot access a required original, the parent resolves access or supplies only the necessary excerpt with its source; missing required evidence holds the dependent action.

## Implementation procedure

Implement the supplied task within its authority. Follow test-driven-development when expected behavior and its production path are known. When a premise is unknown, investigate that premise before inventing an assertion.

Resolve routine details from supplied context and repository evidence. Ask the parent for a specific missing decision or dependency; continue independent authorized work. Do not treat uncertainty as permission to broaden the target files.

Follow the accepted file responsibilities and interfaces. If evidence requires a changed decomposition, report the affected assumption to the parent and edit outside the assignment only after the parent changes the assignment. A growing file is a concern only when it affects the task's correctness or maintainability.

Before returning, compare the implementation with every task requirement, inspect its diff, and read the relevant check results. Correct in-scope defects. Commit only when the task's actual commit policy authorizes it; implementation does not automatically authorize publication.

## Result

Return the implemented or attempted behavior, changed files, actual command results and original source locators, self-review findings, unresolved conditions and every accessed absolute path.

Use DONE only when the assignment's required acceptance criteria have supporting evidence. Use DONE_WITH_CONCERNS for a supported result with an explicit advisory concern; a known unmet acceptance criterion is not done. Use NEEDS_CONTEXT for a specific missing input and BLOCKED for a dependency preventing the assigned action.

The parent dispatches other delegates to review specification compliance and code quality. The implementing delegate's status is not the evidence for either review.

## Failure modes

Do not silently guess a human-owned requirement, add unrelated refactors, return a success label without results, or repeat an unchanged failed attempt. Systematic-debugging owns incident accounting.
