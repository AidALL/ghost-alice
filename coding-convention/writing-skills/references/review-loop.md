# Reviewing a Skill Correction

The response model uses this reference when baseline and candidate evidence disagree or user feedback identifies a possible instruction defect. Apply the parent skill's [Validate and evaluate](../SKILL.md#validate-and-evaluate) procedure; this reference supplies the comparison method.

## Compare the decision

The response model supplies the observed action, required behavior and original source locator to a delegate. The delegate identifies the exact instruction, mapping or premise that could explain the mismatch and returns that comparison to the response model.

Assert objective conditions directly, such as required fields, prohibited outputs or actual call order. For subjective clarity, describe the competing interpretations and the different actions each would produce. A numerical preference score does not settle the instruction's meaning.

## Result and limits

The response model retains the supported contradiction or unresolved interpretation in existing task context and corrects the affected instruction under the parent procedure. A delegate's preference alone does not demonstrate a defect. Verification-before-completion owns evidence scope and stopping.
