# Ghost-ALICE Codex Bootstrap

<!-- Ghost-ALICE governance source: installed-common -->

## Load common governance

Common Ghost-ALICE governance comes from `{{GOVERNANCE_PATH}}` for every project. Before actionable work, the current-session assistant reads that installed common contract and applies its Session Intent and Work Selection sections with the exact intake receipt. Project instruction files supply project-specific rules, which the assistant applies together with the common contract. Explicit user instructions take precedence over this loading procedure.

When no tool is needed (the supplied content or general knowledge supports the answer, and the answer needs no inspection, execution, modification or fresh verification), the response model answers directly without reading the common governance file, even when a hook message points to its sections. When it is unclear whether a tool is needed, the response model treats the request as work and reads the common governance file first.

If the common governance file cannot be read, the response model tells the user once that the Ghost-ALICE contract is missing and continues the user's work without writing Ghost-ALICE ledger records. It does not substitute another session, repository, installation or discovery pointer.
