# Impact Tier

The response model assigns one display tier to each pending change. Adversarial-verification checks the classification along with the decision aid.

| Tier | Observed change | Display treatment |
| --- | --- | --- |
| Trivial | Only whitespace, line endings, indentation, comment text, final newline or frontmatter order changes; frontmatter values stay identical | Show last |
| Semantic | One file's meaning or frontmatter value changes, a section is added/deleted, or a function/variable is added/deleted within that file | Show after structural changes and explain the changed meaning |
| Structural | Multiple files, references/call graph, a skill/user-change scope conflict, a governance file, or frontmatter name/type changes | Show first with the most detailed workflow impact |
