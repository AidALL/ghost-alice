# Delegated Development Example

This illustrative workflow changes a CLI and its maintenance instructions. The requirements, target labels and findings below are fictional; they do not report an executed review or test.

## Accepted sources

The original user requirement is: “Add `--output-format` for text or JSON output. Preserve `--format` as an alias, including its existing default and error behavior. Update the CLI maintenance instructions.”

After the parent asks to continue the accepted work, the user clarifies: “Continue. Keep the old alias visible in help and examples; the maintenance instructions must tell future editors to preserve it.” The parent retains both excerpts or their accessible conversation locators. Permission to continue leaves the original compatibility requirement in force.

The parent's separate interpretation is to route both flag names to the same parser destination and document that shared behavior. The accepted plan assigns the parser, its callers and regression coverage to one task, and the help examples and `docs/cli-maintenance.md` procedure to another. Neither the interpretation nor the plan replaces the original sources.

## Implementation and review target

The parent gives each implementing delegate its complete task and permitted paths. Each delegate returns its actual changed artifacts and evidence to the parent. A report that the alias works does not establish that the handler, help or instructions preserve it.

The parent builds each review assignment from the shared [assignment inputs](../../requesting-code-review/references/code-reviewer.md#assignment-inputs). In this example, target `T1` consists of the committed range from base `B` to head `H`, plus the separately identified uncommitted diff `W1` containing the maintenance instructions. These symbols stand for actual resolved commit IDs and a retained diff in a real assignment. The review checkout must contain that same combined content; a `B..H` diff alone omits `W1`.

The specification reviewer inspects the parser, handler, tests, help and maintenance instructions under the shared [evidence guidance](../../requesting-code-review/references/code-reviewer.md#evidence-by-artifact). Suppose the source shows that both flag names reach the same destination, while `docs/cli-maintenance.md` tells future editors to remove `--format` when adding another format. The reviewer returns that instruction conflict with its line and the original requirement it violates. The reviewer also states which regression results were available; source inspection alone does not show that either flag ran successfully.

The parent checks that finding under [receiving-code-review](../../receiving-code-review/SKILL.md#evaluate-feedback), accepts the conflict and asks the responsible delegate to correct the maintenance procedure within its assigned scope. The parent preserves the reviewer's finding separately from this disposition. The correction produces uncommitted diff `W2`; target `T2` is exactly `B..H` plus `W2`.

The specification reviewer checks the affected instruction at `T2`. Once specification review is supported, the code-quality reviewer receives the same original sources and exact `T2`, including the uncommitted instructions. If any relevant artifact changes, the parent updates the target and reopens the affected review rather than carrying approval from `T1` or silently reviewing only `H`.

## Findings and integration evidence

Suppose the code-quality reviewer recommends removing `--format` to simplify the public interface. The parent compares the proposal with the original compatibility requirement and later clarification, rejects it with those locators, and retains the alias. A review suggestion supplies no authority to change the accepted interface. A separate demonstrated defect would still receive its own accepted, rejected or unresolved disposition under receiving-code-review.

Before integration, the parent compares the combined result at `T2` with the full accepted task: both flag names reach the intended behavior, help and examples expose the alias, and the maintenance procedure tells its recipient to preserve it. Runtime claims require actual results for the relevant defaults, formats and errors. Instruction conformance requires inspection of the changed procedure and its callers. A per-task approval does not establish these interactions.

The parent uses [verification-before-completion](../../verification-before-completion/SKILL.md#done-conditions-and-claim-scope) to judge those claims and the whole result. It retains valid evidence for unchanged behavior with its original source and time, and reopens only evidence affected by the changed target or criterion. Missing runtime or integration evidence remains visible; the illustrative findings above supply none of it.

When the accepted criteria are supported, the parent follows finishing-a-development-branch for the user's authorized integration choice. This example prescribes no mandatory focus sequence or unrelated verification work, and it demonstrates neither installation nor native adoption.
