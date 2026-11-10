# Document Review Assignment

The parent (the session or agent that started the delegate) assigns this bounded review to a delegate and receives its findings. It sends the delegate the text above the two document sections and the one section for the reviewed document.

The parent supplies the absolute document path, the requirements that the human user accepted as the parent's assignment states them with original-source locators, and the review scope after drafting. The parent keeps its interpretation separate from those requirements. The delegate reads the actual document and compares it with those requirements.

Check for missing required content, contradictions, ambiguous behavior that could change implementation, unsupported additions and task boundaries that prevent execution.

Flag an issue only when it could cause an incorrect or blocked implementation. Word-choice preferences and unequal section lengths are advisory. Return the review delegate's `Approved` or `Issues Found` result, with section/task locators, the affected decision, why it matters, supporting evidence and accessed absolute paths.

The review delegate's `Approved` result does not prove implementation or runtime behavior. The parent corrects in-scope defects and requests only an unresolved human-owned decision.

## Specification document

Check component responsibilities, interfaces and acceptance criteria. The review delegate's `Approved` result covers this document's readiness for planning.

## Plan document

Check cross-task types, commands, prerequisites and requirement coverage. The review delegate's `Approved` result covers this document's readiness for execution.
