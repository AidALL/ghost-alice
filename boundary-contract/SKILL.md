---
name: boundary-contract
description: "Writes a boundary contract listing the work's objective, allowed and prohibited paths and actions, done conditions, phase and stop conditions, when task-router requires one."
compatibility:
  - "Python 3.11+ standard library"
---

# Boundary Contract

When the response model selects `boundary-contract: required` while applying task-router, it uses this skill to declare the work's objective, permitted and prohibited surfaces, criteria, phase and stop conditions. Producing the contract does not execute its work. The task-router instructions define when this skill applies; these instructions define how the response model declares the concrete boundary.

## Authority and renewal

Current and prior human instructions supply the authorization envelope: objective, prohibitions, allowed actions and targets, side effects and resources. The response model preserves those instructions' human source and identifies the source of trusted system/project constraints. Intake facts, router decisions, wider focus and an agent's implementation plan do not grant additional authority.

The response model distinguishes a human-owned boundary from a narrower self-declared implementation limit. When a necessary action exceeds only the narrower implementation limit, the response model renews that contract inside existing authority and continues. When the action exceeds a human-owned boundary or requires a missing decisive input, the response model asks for that exact decision before the dependent action. A grant changes only its stated scope; denial parks that branch; no answer leaves it waiting.

An absent explicit resource cap is not a user-approved limit invented by the response model.

The response model preserves the terminal objective separately from investigative or implementation means. It must not narrow a committed full surface to selected examples. When the request covers every member of a set, the coverage criterion states the condition that defines the set. Discovery lists the members; the criterion does not require the members or their count before discovery.

## Declare the contract

The response model writes the contract from established user instructions, supplied context and observed source facts. It must not guess filenames or invent allowed surfaces. Unknown targets require discovery. Every criterion describes a requested outcome; incidental methods, examples, artifact counts and verification mechanics become criteria only when required by the user or applicable contract.

Human explanations follow the current user language. Keep field names in English, including enum values and literal protocol tokens.

```text
[boundary-contract]
- id: <stable-id>
- phase: discovery | execution | verification
- objective: <terminal work objective>
- explicit-non-goals:
  - <non-goal>
- allowed-surface:
  - <allowed read or write surface>
- prohibited-surface:
  - <prohibited path, layer or action>
- locked-decisions:
  - <decision> [source: user-said | interpreted-as | previous-tool | system-doc | unknown]
- acceptance-criteria:
  - <criterion-id>: <verifiable outcome> [source: user-said | interpreted-as | previous-tool | system-doc]
- open-questions:
  - <unresolved question>
- stop-conditions:
  - <condition stopping dependent work>
- next-allowed-actions:
  - <permitted next action>
```

The response model includes stop conditions in every contract. Before using an additional surface, it applies Authority and renewal above.

## Phase rules

The phase names the current authority state. The targets known at that moment determine it; it is not a planned sequence.

| Phase | Entry condition | Permitted work |
| --- | --- | --- |
| discovery | Target files, modules or verification methods are unknown | Read-only discovery on named action surfaces; no implementation |
| execution | Discovery or supplied evidence establishes the targets | Work on concrete allowed files/directories under the declared criteria |
| verification | Implementation is ready for checking | Declared commands, screenshots or visual checks; no undeclared implementation |

A phase change preserves the original done conditions. If verification reveals a needed edit, the response model renews the affected execution contract before editing.

## Source rules

The response model assigns a source to each atomic proposition and splits statements with different origins.

- `user-said`: the human directly supplied the instruction or approval.
- `interpreted-as`: the response model selected a conservative interpretation for the current phase.
- `previous-tool`: a tool or runtime supplied the fact/control, including generated text transported with a user role. A generated approval digest is not human approval.
- `system-doc`: AGENTS.md, a skill or a policy requires the condition.
- `unknown`: provenance is unresolved; the response model cannot use this as an execution-phase lock.

For example, “preserve protected.txt as requested” may be user-said, while “preserve the hook approval-generation value” is previous-tool. Their shared transport does not merge their authority.

## Tool checkpoint

While the contract is active, the response model maps every tool call to an allowed-surface item or a next-allowed-action before making it. A surfaced tool checkpoint shows that mapping:

```text
- contract-ref: <contract ID>
- contract-check: <allowed-surface item or next-allowed-action>
```

If the proposed call maps to neither field, the response model keeps that call closed and applies Authority and renewal.

## Exact protected-file evidence

When the execution contract protects existing files or requires exact modification-time comparison, the response model invokes `scripts/file_guard.py`, resolved relative to this skill, before dependent writes. Read its help only when invocation details are needed.

```text
python3 <loaded-skill-directory>/scripts/file_guard.py capture --path <protected-file> [--path <another-protected-file>]
python3 <loaded-skill-directory>/scripts/file_guard.py check --token <token returned by capture>
```

The helper captures content, exact timestamp, identity and mode without writing task files. The response model retains the returned string token verbatim and passes it through structured arguments or proper shell quoting. It must not decode or rebuild timestamp fields, transmit nanoseconds or large identity integers as JavaScript numbers, or reconstruct lost precision from rounded values.

The response model captures once before the protected operation, checks immediately before a dependent write, and checks afterward when preservation is a done condition. A failed comparison stops the dependent write; the response model investigates against the original baseline rather than replacing it. If the helper is unavailable, capture and comparison use one exact-integer process or original integer strings.

The helper observes regular files at one point in time; it is neither an atomic lock nor authorization or completion proof. It rejects symlinks and parent traversal and does not cover directory membership. The response model retains other contract-specific checks.

## Failure modes and proof

The response model must not label an interpretation user-said, perform implementation under discovery, or omit coverage from a full-set objective. Peripheral checks support only criteria they actually measure.

The response model following the selected procedure executes the action and judges its returned evidence against the criteria. For discretionary checks, evidence source/time reuse and native completion, apply `../coding-convention/verification-before-completion/SKILL.md`. Failed-round counts and additional-attempt or resource allowances belong to the incident workflow in `../coding-convention/systematic-debugging/SKILL.md#incident-accounting-and-continuation`. Contract renewal does not reset them or require replay of completed business work.
