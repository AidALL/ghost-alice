# Tool Output Semantics

[🇺🇸 English](./tool-output-semantics.md) | [🇰🇷 한국어](../ko/policies/tool-output-semantics.md)

Tool results distinguish an observation, an authenticated review, and an executed action. Human labels help readers; the documented machine fields govern consumers. Compatibility labels do not replace those fields.

## Host and hook responsibilities

The native host validates tool arguments and enforces permission, the role that [Responsibilities and flow](../../architecture.md#responsibilities-and-flow) assigns to it. A reported argument error determines the correction to an authorized call. Governance hooks inspect work authority and evidence; they do not infer tool capabilities from serialized text.

Hook status distinguishes a matching installation, configuration drift, disabled hooks, missing components and an unsupported platform. Drift requires examining the affected installed binding; it does not mean that every hook is absent. The [platform compatibility contract](installer-platform-compatibility-matrix.md) owns installation decisions.

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
