# Diagnosing and Correcting Instruction Failures

Use this reference to decide whether an observed output or rule-application failure warrants an instruction repair, and to select the repair form when the evidence supports one.

## Procedure

Compare the [preserved baseline](../SKILL.md#baseline-and-scope) with the requirement that the human user accepted, the governing instruction and required task inputs. Identify whether the evidence supports missing, ambiguous or conflicting guidance, or a specific interpretation to repair. The failed outcome alone does not establish that the instruction caused it.

When clear guidance was not followed or its required input was omitted, correct that task's application or input under its existing procedure. For example, if the review guidance already requires a head commit but the parent (the session or agent that started the delegate) omitted it, supply the resolved head through the existing assignment instead of adding another template. If the comparison does not yet identify an instruction defect, retain the guidance and investigate the unresolved task condition.

For supported wording ambiguity, first identify the competing noun referents or actions the sentence permits in its task context. Resolve those readings against the requirement that the human user accepted and the owning procedure, then rewrite the responsible instruction in place. Retain a term whose referenced procedure already determines the required action; a word such as “verify” is not defective by itself.

For an identified instruction defect, select the repair form below. The example instructions illustrate forms within what the human user's instructions authorize for the affected task and its applicability conditions.

| Supported instruction defect | Repair form | Example instruction |
| --- | --- | --- |
| Required output structure is missing or ambiguous | Put a concrete template at the producing step, naming the fields or order that failed. | When returning a comparison, the response model gives the user a table with Option, Requirement and Evidence columns. |
| Guidance omits a necessary input or output handoff | Name the missing value, its authoritative source or recipient, and the step that consumes or produces it. | Before dispatching a review, the parent establishes the target from the task it assigned and the repository state, then supplies it to the delegate under requesting-code-review's assignment procedure. |
| A trigger or action permits the wrong branch | State the trigger and the action for each relevant branch. | For a committed review target, the delegate inspects the supplied base-to-head range; for separately identified uncommitted work, it inspects that working-tree diff. |
| An interpretation bypasses an applicable rule | Rewrite the misread decision in place before adding a separate prohibition. | Under an applicable RED-first contract, a response model that wrote premature production code removes only its own authorized change before rebuilding from the test, preserving local changes already present in the checkout that this work did not make; git does not record who made the change. |

A rationalization table or red-flag list is useful when several observed cases expose distinct conditions. Update the trigger description only when discovery itself failed.

After selecting the repair, follow writing-skills' [Validate and evaluate](../SKILL.md#validate-and-evaluate) procedure for the correction.

## Failure modes

Do not use “no exceptions” to erase the human user's instructions or the rule's applicability conditions; the common contract defines instruction precedence. The actual output or action identifies whether the instruction defect remains.

Keep examples reusable and complete. Prefer a concrete decision diagram over vague nodes, and keep code blocks and scripts outside diagram labels.
