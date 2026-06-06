# Human Review Loop

This document defines the human-feedback-driven iteration loop used in the REFACTOR stage of `writing-skills`.
## Contents

- [When to read this](#when-to-read-this)
- [Core principles](#core-principles)
- [Minimal loop](#minimal-loop)
- [Standard loop](#standard-loop)
- [Record format](#record-format)
- [Prohibited](#prohibited)
- [Tool usage principle](#tool-usage-principle)


## When to read this

- When you want to compare baseline and with-skill results side by side.
- When you are unsure what format to collect user feedback in.
- When you need to decide how far to attach assertions.

## Core principles

- Feedback is diagnostic information. It is not completion evidence.
- An evaluation without a baseline creates an illusion.
- Mix qualitative feedback with quantitative checks, but do not force quantification.
- After a fix, always run the same scenario again.

## Minimal loop

1. Pick 2 to 3 pressure scenarios.
2. Save the baseline result.
3. Save the with-skill result.
4. Have the user compare the two.
5. Record the points of dissatisfaction.
6. Modify the skill.
7. Run the same scenario again.

This minimal loop alone is enough to sufficiently improve most rule skills.

## Standard loop

Turn only the objectifiable items below into assertions:
- A specific section must exist.
- A forbidden expression must not appear.
- The specified format must be followed.
- A specific tool or order must actually be used.

Items that are hard to objectify:
- A sentence is "more persuasive".
- It "looks good" overall.
- Creativity is sufficient.

Handle items like these through user comments only.

## Record format

For each scenario, leave at least the following:

```yaml
scenario: "rule compliance under fatigue plus time pressure"
baseline_failure:
  - "claims completion without verification"
with_skill_result:
  - "claims the result only after running the verification command"
user_feedback:
  - "improved, but still overconfident right before the deadline"
next_change:
  - "strengthen the fatigue and time-pressure red flag wording"
```

## Prohibited

- Declaring satisfaction after seeing only the with-skill result, without a baseline.
- Skipping the re-run just because feedback was received.
- Attaching forced assertions to a subjective skill.
- Skipping the review loop itself just because there is no viewer or benchmark.

## Tool usage principle

- A viewer, grader, and benchmark are optional tools.
- The essence is the baseline vs with-skill comparison and the re-run.
- If a tool simplifies the loop you may use it, but if the tool itself becomes a required dependency that is excessive.
