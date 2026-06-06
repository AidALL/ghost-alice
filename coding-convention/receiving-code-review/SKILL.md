---
name: receiving-code-review
description: Use when receiving code review feedback before implementing suggestions, especially when feedback is ambiguous or technically questionable. Enforces technical rigor and verification instead of performative agreement or blind implementation.
compatibility:
  - "Python 3.11+ standard library"
---

# Receiving Code Review
## Contents

- [Overview](#overview)
- [Response Pattern](#response-pattern)
- [Forbidden Responses](#forbidden-responses)
- [Handling Ambiguous Feedback](#handling-ambiguous-feedback)
- [Handling by Source](#handling-by-source)
  - [From the User](#from-the-user)
  - [From an External Reviewer](#from-an-external-reviewer)
- [YAGNI (You Aren't Gonna Need It) Check for "Professional" Features](#yagni-you-arent-gonna-need-it-check-for-professional-features)
- [Implementation Order](#implementation-order)
- [When to Rebut](#when-to-rebut)
- [Acknowledging Correct Feedback](#acknowledging-correct-feedback)
- [Gracefully Correcting a Rebuttal](#gracefully-correcting-a-rebuttal)
- [Common Mistakes](#common-mistakes)
- [Examples](#examples)
- [Replying on a GitHub Thread](#replying-on-a-github-thread)
- [Conclusion](#conclusion)


## Overview

A code review demands technical evaluation, not an emotional performance.

○ Core principles

- Verify before implementing.
- Ask before assuming.
- Technical accuracy over social comfort.

## Response Pattern

```
When you receive code review feedback:

1. Read: read all the feedback without reacting
2. Understand: restate the requirement in your own words (or ask)
3. Verify: check it against the reality of the codebase
4. Evaluate: is it technically sound for this codebase?
5. Respond: technical acknowledgment or a grounded rebuttal
6. Implement: one item at a time, testing each one
```

## Forbidden Responses

□ Never do this

- "Absolutely right!" (an explicit violation of CLAUDE.md)
- "Good point!" / "Great feedback!" (performative)
- "I'll implement it now" (before verifying)

□ Instead

- Restate the technical requirement
- Ask a clarifying question
- Rebut with technical grounds if it is wrong
- Just start the work (action over words)

## Handling Ambiguous Feedback

```
If any item is ambiguous:
  Stop. Do not implement anything yet.
  Request clarification on the ambiguous item.

Reason: items can be related to each other. Partial understanding = wrong implementation.
```

Example

```
User: "Fix 1 through 6"
You understand 1, 2, 3, and 6. Items 4 and 5 are ambiguous.

❌ Implement 1, 2, 3, 6 first and ask about 4, 5 later
✅ "I understand 1, 2, 3, and 6. I need clarification on 4 and 5 before proceeding."
```

## Handling by Source

### From the User

- Trusted. Implement after understanding.
- If the scope is ambiguous, still ask.
- No performative agreement.
- Go straight to action or give a technical acknowledgment.

### From an External Reviewer

```
Before implementing:
  1. Check: is it technically correct for this codebase?
  2. Check: does it break existing functionality?
  3. Check: is there a reason for the current implementation?
  4. Check: does it work on all platforms and versions?
  5. Check: does the reviewer understand the full context?

If the suggestion looks wrong:
  Rebut with technical grounds.

If you cannot verify it easily:
  Say so: "I cannot verify this without X. Which do you want: [investigate / ask / proceed]?"

If it conflicts with the user's earlier decision:
  Stop and discuss with the user first.
```

○ User rule: "External feedback. Be skeptical but check carefully."

## YAGNI (You Aren't Gonna Need It) Check for "Professional" Features

```
When a reviewer suggests "implement it properly":
  grep the codebase for the actual usage.

  Not used: "This endpoint is never called. Remove it (YAGNI)?"
  Used: then implement it properly.
```

○ User rule: "Both you and the reviewer report to me. Do not add features that are not needed."

## Implementation Order

```
Multi-item feedback:
  1. Clarify the ambiguous ones first.
  2. Implement in this order:
     - Blocking issues (breakage, security)
     - Simple fixes (typos, imports)
     - Complex fixes (refactoring, logic)
  3. Test each fix individually.
  4. Verify there is no regression.
```

## When to Rebut

Rebut in the following cases.

- The suggestion breaks existing functionality.
- The reviewer lacks the full context.
- A YAGNI violation (a feature that is not used).
- It is technically inaccurate for this stack.
- A legacy or compatibility reason exists.
- It conflicts with the user's architecture decision.

□ How to rebut

- With technical grounds, not defensively.
- Specific questions.
- Reference a working test or working code.
- Bring in the user if it is an architecture matter.

○ If rebuttal feels awkward, slow down and write the technical evidence first.

## Acknowledging Correct Feedback

When the feedback is right

```
✅ "Fixed. [short note on what changed]"
✅ "Good catch. [specific issue]. Fixed at [location]."
✅ [just fix it and show it in code]

❌ "Absolutely right!"
❌ "Good point!"
❌ "Thanks for catching that!"
❌ "Thanks for [anything]"
❌ Any kind of thank-you expression
```

○ Why thank-yous are forbidden: action speaks. Just fix it. The code itself shows that the feedback was heard.

○ If you catch yourself about to write "thanks": delete it. State the fact of the fix instead.

## Gracefully Correcting a Rebuttal

When you rebutted but were wrong

```
✅ "You were right. I checked [X] and it turned out to be [Y]. Implementing now."
✅ "After verifying, you are right. My initial understanding was wrong, because [reason]. Fixing now."

❌ A long apology
❌ Defending why you rebutted
❌ Over-explaining
```

Correct the record factually and proceed.

## Common Mistakes

| Mistake | Fix |
|------|------|
| Performative agreement | State the requirement or just act |
| Blind implementation | Check against the codebase first |
| Batch processing then testing | One at a time, test each one |
| Assuming the reviewer is right | Check whether it breaks |
| Avoiding a rebuttal | Technical accuracy > comfort |
| Partial implementation | Clarify all items first |
| Proceeding when you cannot verify | State the limit, request direction |

## Examples

□ Performative agreement (bad)

```
Reviewer: "Remove the legacy code"
❌ "Absolutely right! I'll remove it..."
```

□ Technical verification (good)

```
Reviewer: "Remove the legacy code"
✅ "Checking... the build target is 10.15+, and this API needs 13+. The legacy code is needed for backward compatibility. The current implementation has the wrong bundle ID. Fix that, or drop pre-13 support?"
```

□ YAGNI (good)

```
Reviewer: "Implement proper metrics tracking with a DB, date filters, and CSV export"
✅ "I grepped the codebase. This endpoint has no callers. Remove it (YAGNI)? Or did I miss a usage?"
```

□ Ambiguous item (good)

```
User: "Fix 1 through 6"
You understand 1, 2, 3, 6 and 4, 5 are ambiguous.
✅ "I understand 1, 2, 3, 6. I need clarification on 4 and 5 before implementing."
```

## Replying on a GitHub Thread

When you reply to a GitHub inline review comment, reply on the comment thread (`gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies`). Do not reply as a top-level PR comment.

## Conclusion

External feedback = a suggestion to evaluate, not a command to obey.

Verify. Doubt. Only then implement.

No performative agreement. Technical rigor always.
