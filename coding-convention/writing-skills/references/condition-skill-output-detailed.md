# Trigger, Procedure and Output

Use this reference when a skill's description is ambiguous or its body mixes discovery with execution.

## Description

Use concrete task purposes, symptoms and relevant domain terms. State a technology restriction when it actually limits applicability. Write in third person.

For example, “Test first, implement, refactor” summarizes the body's workflow and does not say when the skill is used.

Keep the complete procedure in the body.

## Body and references

The body supplies the executable handoff defined in [Author the package](../SKILL.md#author-the-package), not only an acting-agent label. For a two-stage review, identify the first review's actual returned findings and the condition under which the parent assistant sends the accepted target and those findings to the second reviewer. The second review cannot silently replace the first.

Use search terms the intended user or agent will encounter, including concrete error names and domain vocabulary. Name the skill for its responsibility rather than a generic label such as helper.

Replace repeated policy with a link to the document and section that define the applicable procedure. Label a required prerequisite as required and identify the point where it applies; label optional background as optional. Avoid host-specific forced-load syntax.

## Brevity

Reduce words that do not change the decision, not instructions that establish authority or evidence. Move long details to conditionally loaded references and keep one complete representative example. Word counts are an editing aid, not a substitute for required contract coverage.

## Evaluation

Use trigger-eval.md when the description's selection behavior is the live uncertainty. Use writing-skills' application workflow when the body is wrong. A better keyword match does not establish correct execution.
