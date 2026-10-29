# Skill Authoring Best Practices

This is a local authoring summary, not a copy of an upstream specification or a claim about the current host. The upstream [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) and [authoring guidance](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) provide provider context. The project compliance checklist owns local validation. Check current official documentation before making a version-specific platform claim.

## Instruction design

The author supplies information the assistant reading the finished skill needs for the task: non-obvious constraints, domain facts, exact fragile sequences and the conditions for choosing among alternatives. Omit generic explanations that do not change that assistant's decision.

Match discretion to the operation. Give judgment-based work an objective and acceptance criteria; give a preferred pattern parameters and boundaries; give a fragile operation an exact sequence with named preconditions. An exact command is appropriate only when its paths, inputs, permissions and environment are established.

Use one term for each concept. State the actor, action and object in procedural prose. Distinguish what the agent must read from what it must execute. A script reference alone must not imply execution.

## Discovery and package structure

Follow writing-skills' [Author the package](../SKILL.md#author-the-package) requirements for metadata, core guidance and conditional references. Put distinct domain schemas or examples in a reference when readers need only that domain. Keep direct loading conditions visible so a reference chain cannot hide a prerequisite.

## Workflow and output examples

For an operational handoff, apply [the authoring contract](../SKILL.md#author-the-package): the reader must know where the input comes from, its required shape, the actual call or transport, the returned result, its owner and the next decision. For example, the assistant filling a form reads the actual field schema from the exposed form tool, maps user-supplied values into that schema, submits the validated mapping with the tool's documented arguments, and inspects its returned document or validation errors. A mapping failure requires correcting that mapping.

Use an exact template when an output consumer requires a schema. For narrative work, give a default structure with the conditions under which the agent may adapt it. One complete input/output example should demonstrate the expected content and level of detail.

Choose a concrete default method when the domain contract supports one. Name an alternative only with the condition that makes the default unsuitable. Do not present an unexplained menu of libraries.

## Evaluation

Writing-skills' [Validate and evaluate](../SKILL.md#validate-and-evaluate) procedure owns baseline, package validation and affected behavior checks. During those checks, observe discovery, reference navigation, missing prerequisites and repeated reads to identify which instruction structure needs correction. Source formatting and user feedback alone do not demonstrate installed behavior.

## Executable support

Document dependencies and use the actual host's installed tools and permissions. Package metadata does not establish that a library, network capability or MCP server is available. Use the exact tool name and schema exposed by the host; provider-specific examples are not portable invocation contracts.

A utility script should validate inputs and return actionable errors for expected failure conditions. Do not silently invent data, create files or replace denied access with empty content unless that behavior is the operation's accepted contract. Explain configuration bounds such as timeouts and retries from the task's requirements.

Prefer a maintained deterministic helper when it implements an established repeated operation. State its inputs, command, outputs and error behavior. For batch or consequential operations, validate a structured proposed change before applying it when that mapping is a live source of error.

When layout matters, render the output and inspect the relevant pages or regions. When the claim concerns data or code behavior, use evidence appropriate to that criterion; a visual preview does not replace it.

## Failure modes

Do not copy time-sensitive support claims, host tool names or installation instructions into canonical guidance without their applicability conditions. Keep historical examples clearly historical. Do not claim that stronger wording, a script, a checklist or a passing example guarantees compliance.

Before distributing a package, complete the project's required validation and the affected evaluation.
