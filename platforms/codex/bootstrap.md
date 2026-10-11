# Ghost-ALICE Codex Bootstrap

<!-- Ghost-ALICE governance source: installed-common -->

## Load common governance

Common Ghost-ALICE governance comes from `{{GOVERNANCE_PATH}}` for every project. Before actionable work, the current-session assistant reads that installed common contract. Project instruction files supply project-specific rules, which the assistant applies together with the common contract. Explicit user instructions take precedence. A procedure does not create permission, evidence, or a completed result.

If the common governance file cannot be read, the current-session assistant reports the missing contract and does not perform work that requires it. The current-session assistant must not substitute another session, repository or installation.

For a question answerable entirely from supplied conversation, the current-session assistant returns the bounded answer without reading the common governance file or using tools. A status question or correction does not cancel continuing authorized work. The current-session assistant honors an explicit stop, pause, cancellation or replacement.

## Apply the common contract

For normal work, the current-session assistant applies the common contract's First-Entry Contract using the intake hook's exact session receipt. That contract identifies the procedures for work selection, authorization, evidence and completion. The native host controls permission to execute tools. Hooks execute and retain strict audit independently of displayed guidance.
