# Mermaid Flowchart Conventions

The skill author loads this reference when adding or editing a workflow diagram. Embed the diagram directly in Markdown with a `~~~mermaid` fence and `flowchart TD`. The examples below illustrate notation; their actors, commands and conditions are diagram data, not instructions to execute a task.

## Node types and shapes

| Role | Mermaid notation | Label example |
| --- | --- | --- |
| Decision | `D{"Does the condition hold?"}` | “Is the test passing?” |
| Action | `A["Actor performs an action on an object"]` | “The assistant writes the test.” |
| Command | `C["The host runs the literal command"]` | “The host runs npm test.” |
| State | `S(["Observed state"])` | “The test is failing.” |
| Warning | `W["STOP: named condition"]` | “STOP: selected files exceed authorized scope.” |
| Entry or exit | `E(["Process starts"])` or `X(["Process complete"])` | Name the process and its start or completion state. |

Use short, stable node IDs and quoted labels. Name the actor and object for actions, and state the condition for decisions. Keep literal API or command names in their responsible actor's label. Use `<br/>` to divide a long label at a meaningful boundary. Warning and entry/exit labels carry their meaning in words; they do not depend on color or a special shape.

## Edge labels

Label each edge with the condition, input or output for the transfer. Binary decisions use `yes` and `no` after a complete question. Multiple-choice decisions name their alternatives and fallback. A dotted arrow can distinguish a trigger from an ordinary process step.

~~~mermaid
flowchart TD
  B{"Does the sample binary condition hold?"} -->|yes| Y["The example actor follows the yes branch"]
  B -->|no| N["The example actor follows the no branch"]
  M{"Which sample condition holds?"} -->|condition A| A["The example actor selects option A"]
  M -->|condition B| C["The example actor selects option B"]
  M -->|otherwise| F["The example actor selects option C"]
  P(["Process A is complete"]) -.->|triggers| Q(["Process B starts"])
~~~

## Naming patterns

| Label type | Pattern and example |
| --- | --- |
| Question | Ask a complete question ending in `?`, such as “Can the assistant reproduce the failure?” |
| Action | Name an actor, action and object, such as “The assistant searches the error trace for the source value.” |
| Command | Include the literal command, such as “The host runs git status” or “The host runs npm run build.” |
| State | Describe the observed situation, such as “The build is complete” or “The test is failing.” |

## Process structure template

This example shows a trigger, an initial decision, main and alternative actions, a command and a final decision. The sample conditions stand in for the actual named conditions in a real workflow.

~~~mermaid
flowchart TD
  T(["Trigger: the sample event occurs"]) -->|Sample event| I{"Does the initial sample condition hold?"}
  I -->|yes| M["The example actor performs the main action"]
  I -->|no| A["The example actor performs the alternative action"]
  M -->|Main action complete| C["The example host runs git status"]
  C -->|Returned status| D{"Does the second sample check pass?"}
  D -->|ok| E(["Process complete"])
  D -->|problem| W["STOP: do not perform the sample action"]
  A -->|Alternative action complete| E
~~~

## Shape selection

Select the first matching role in this order. A command and a warning can share a rectangle with an action because their labels identify their different meanings.

| Condition | Shape and label |
| --- | --- |
| The node is a decision. | Use a diamond with the question. |
| Otherwise, the node is a command. | Use a rectangle naming the actor and literal command. |
| Otherwise, the node is a warning. | Use a rectangle beginning with `STOP:` and name the condition. |
| Otherwise, the node is an entry or exit. | Use a rounded node naming the process start or completion. |
| Otherwise, the node is a state. | Use a rounded node describing the observed state. |
| Otherwise. | Use a rectangle naming the actor, action and object. |

## Specific and vague examples

The specific example shows the failed-test observation, error reading, reproduction decision and command on the `yes` branch. The separate warning illustrates a label; it adds no branch to that example.

~~~mermaid
flowchart TD
  F(["The test failed"]) -->|Failure output| R["The assistant reads the error message"]
  R -->|Error and reproduction conditions| D{"Can the assistant reproduce the failure?"}
  D -->|yes| C["The host runs git diff HEAD~1"]
  W["STOP: prerequisite result unresolved"]
~~~

The following labels are counterexamples, not actions to perform. Their sequence leaves every handoff unspecified.

~~~mermaid
flowchart TD
  B1["Something wrong"] --> B2["Fix it"]
  B2 --> B3["Check"]
  B3 --> B4["Run command"]
~~~

| Vague label | Missing information |
| --- | --- |
| “Something wrong” | Name the observed state. |
| “Fix it” | Name the actor, action and affected object. |
| “Check” | State the decision condition when a branch is intended. |
| “Run command” | Name the responsible actor and literal command. |
