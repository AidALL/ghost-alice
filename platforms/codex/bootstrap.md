# Ghost-ALICE Codex Bootstrap

<!-- Ghost-ALICE governance source: installed-common -->

## Load common governance

Common Ghost-ALICE governance comes from `{{GOVERNANCE_PATH}}` for every project. Before actionable work, the response model reads that installed common contract and applies its Session Intent and Work Selection sections with the exact session metadata. Project instruction files supply project-specific rules, which the response model applies together with the common contract. An instruction that the human user enters in the conversation takes precedence over this loading procedure.

When no tool is needed (the supplied content or general knowledge supports the answer, and the answer needs no inspection, execution, modification or fresh verification), the response model answers directly without reading the common governance file. When it is unclear whether a tool is needed, the response model treats the request as work and reads the common governance file first.

If the common governance file cannot be read, the response model tells the user once that the Ghost-ALICE contract is missing and continues the user's work without writing Ghost-ALICE ledger records. It does not substitute another session, repository or installation, or the shared pointer `current-session.json`, for the ones this session was given, even when they are missing.
