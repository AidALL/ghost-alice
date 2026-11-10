# Impact Tier

The response model assigns one display tier to each pending change. The response model passes the classification through adversarial-verification together with the content it prepares for the human user's decision.

| Tier | Observed change | Display treatment |
| --- | --- | --- |
| Trivial | Only whitespace, line endings, indentation, comment text, final newline or frontmatter order changes; frontmatter values stay identical | Show last |
| Semantic | One file's meaning or frontmatter value changes, a section is added/deleted, or a function/variable is added/deleted within that file | Show after structural changes and explain the changed meaning |
| Structural | Multiple files, references/call graph, a scope conflict between the skill and an external change, a governance file, or frontmatter name/type changes | Show first with the most detailed workflow impact |

An external change is a change that the installer finds in an installed target after install and that its records do not hold; the installer does not record who made the change.
