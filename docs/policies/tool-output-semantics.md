# Tool Output Semantics

[🇺🇸 English](./tool-output-semantics.md) | [🇰🇷 한국어](../ko/policies/tool-output-semantics.md)

Tool results distinguish an observation, an authenticated review, and an executed action. Human labels help readers; the documented machine fields govern consumers. Compatibility labels do not replace those fields.

## Host and hook responsibilities

The host validates tool arguments and enforces its permission rules, the role that [Responsibilities and flow](../../architecture.md#responsibilities-and-flow) assigns to it. The response model uses a reported argument error to correct the call it sends within what the human user's instructions authorize. Governance hooks read the records that Core code stores in the intent ledger (the fingerprint of the input the host delivered, the acceptance criteria with their source labels, and the response route) and the evidence. Those records do not by themselves establish the human user's grant; governance hooks do not infer tool capabilities from serialized text.

Hook status distinguishes a matching installation, configuration drift, disabled hooks and missing components. Drift requires examining the affected installed binding; it does not mean that every hook is absent. The [platform compatibility contract](installer-platform-compatibility-matrix.md) owns installation decisions.

Counts retain their declared unit. A unique finding count differs from physical occurrences, and an absent count is unknown.

## Review, output and continuation

[Evidence and native delivery](../../architecture.md#evidence-and-native-delivery) describes the model's two judgments and Core's authentication. A review receipt returns status and references; it does not repeat the answer body. An unfavorable review or missing prerequisite remains unresolved under the [review procedure](../../coding-convention/verification-before-completion/references/response-review.md).

```mermaid
flowchart LR
  M["Response model: claims and whole-answer judgments"] --> C["Core: authenticate bindings"]
  C -->|Status and references| M
  M --> H["Native host: display answer"]
  H -->|Stop event| S["Core Stop script"]
```

Authentication, recording and the judgment of a criterion are separate results.

[Hook calls](../../architecture.md#hook-calls) describes native Stop. The Stop notice does not establish answer visibility or create missing judgments. See the [session gate owner map](session-gate-matrix.md) for responsibilities.
