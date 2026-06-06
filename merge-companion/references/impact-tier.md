# Impact tier

The tier classification is for UI ordering and display (impact-tier display classification). Because adversarial-verification fires with no exception, no tier bypasses verification.

## Trivial

- only whitespace, line endings, or indentation changed
- frontmatter field order changed (values identical)
- only comment text changed
- end-of-file newline added or removed

UI ordering: last. The recommended default user decision is "merge".

## Semantic

- the body meaning of a single file changed
- a frontmatter value changed
- a new section added or an existing section deleted
- a function or variable added or deleted (within one file)

UI ordering: middle. Decide with care.

## Structural

- a change spanning multiple files
- a call-graph change (one file adds or removes a reference to another file)
- a scope conflict between a new skill and a user change (for example, a helper the user created overlapping with a new sub-skill feature)
- a change to a governance file such as AGENTS.md
- a frontmatter `name` or `type` change

UI ordering: first. The user decision needs the most care. The AI analysis is the most detailed.

## Measuring classification accuracy

Because the tier classification itself is also performed by the AI, misclassification is possible. The impact-tier classification result is included in the adversarial-verification verification output and is confirmed by multi-agent consensus.
