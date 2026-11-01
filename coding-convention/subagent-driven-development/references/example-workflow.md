# Delegated Development Example

This illustrative workflow changes a CLI and its maintenance instructions. The requirements, target labels and findings below are fictional; they do not report an executed review or test.

## Accepted sources

The original user requirement is: “Add `--output-format` for text or JSON output. Preserve `--format` as an alias, including its existing default and error behavior. Update the CLI maintenance instructions.”

After the parent asks to continue, the user clarifies: “Continue. Keep the old alias visible in help and examples; the maintenance instructions must tell future editors to preserve it.” Both excerpts remain inputs to the review.

The parent separately proposes routing both flag names to the same parser destination. The accepted plan assigns the parser and regression coverage to one task, and help examples and `docs/cli-maintenance.md` to another.

## Implementation and review target

The parent gives each implementing delegate its complete task and permitted paths, then receives the changed artifacts and evidence.

The parent builds each review assignment from the shared [assignment inputs](../../requesting-code-review/references/code-reviewer.md#assignment-inputs). In this example, target `T1` consists of the committed range from base `B` to head `H`, plus the separately identified uncommitted diff `W1` containing the maintenance instructions. These symbols stand for actual resolved commit IDs and a retained diff in a real assignment. The review checkout must contain that same combined content; a `B..H` diff alone omits `W1`.

The specification reviewer inspects the parser, handler, tests, help and maintenance instructions under the shared [evidence guidance](../../requesting-code-review/references/code-reviewer.md#evidence-by-artifact). Suppose both flag names reach the same destination, but `docs/cli-maintenance.md` tells future editors to remove `--format` when adding another format. The reviewer returns the conflicting instruction, its line and the original requirement, distinguishing source inspection from available regression results.

The parent checks the finding under [receiving-code-review](../../receiving-code-review/SKILL.md#evaluate-feedback), accepts the conflict and asks the responsible delegate to correct the procedure. The finding and this disposition remain separate. The correction produces uncommitted diff `W2`; target `T2` is exactly `B..H` plus `W2`.

The specification reviewer checks the affected instruction at `T2`. Once specification review is supported, the code-quality reviewer receives the same sources and exact `T2`, including the uncommitted instructions. A further target change follows the existing [affected-evidence procedure](../../verification-before-completion/references/verify-or-reuse.md#decision-flow).

## Findings and integration evidence

Suppose the code-quality reviewer recommends removing `--format` to simplify the interface. The parent rejects that proposal against the original requirement and later clarification, retaining both the alias and the source locators for the rejection.

Before integration, the parent uses [verification-before-completion](../../verification-before-completion/SKILL.md#done-conditions-and-claim-scope) to compare the combined result at `T2` with the task: actual results cover the relevant defaults, formats and errors for both flag names; help and examples expose the alias; and the maintenance procedure tells its recipient to preserve it. Missing observations remain unresolved. Once the accepted criteria are supported, finishing-a-development-branch carries out the user's authorized integration choice.
