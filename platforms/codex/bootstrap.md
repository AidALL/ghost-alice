# Ghost-ALICE Codex Bootstrap

<!-- Ghost-ALICE governance source: installed-common -->

## Load common governance

Common Ghost-ALICE governance comes from `{{GOVERNANCE_PATH}}` for every project. Before actionable work, the current-session assistant reads that installed common contract and applies its Session Intent and Work Selection sections with the exact intake receipt. Project instruction files supply project-specific rules, which the assistant applies together with the common contract. Explicit user instructions take precedence over this loading procedure.

For a question answerable entirely from supplied conversation, the current-session assistant returns the bounded answer without reading the common governance file or using tools, even when a hook message points to its sections.

If the common governance file cannot be read, the current-session assistant reports the missing contract and does not perform work that requires it. It does not substitute another session, repository or installation.
