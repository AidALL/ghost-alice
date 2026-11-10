# Ghost-ALICE Core Architecture

[🇺🇸 English](architecture.md) | [🇰🇷 한국어](architecture_ko.md)

Ghost-ALICE Core is the collection of how the generative backend works under this philosophy: the common contract, the skills, and the Core code (hooks, ledger and context gateway). An adapter translates Core for one host, such as Claude Code or Codex: the instruction template, hook event names, transcript format and configuration. The host runs the generative backend and its tools, and the response model follows Core's instructions, performs the work that the human user's instructions authorize and judges its results. The [design philosophy](https://github.com/AidALL/ghost-alice/wiki/Design-Principles) makes the human user's purpose the standard for judgment; agreement between procedures or records alone does not prove success.

## Structure and boundaries

```mermaid
flowchart TB
  subgraph GA["Ghost-ALICE"]
    C["Core: how the generative backend works under this philosophy<br/>common contract, skills, Core code"]
    AC["Claude Code adapter<br/>instruction template, hook events, transcript format, settings"]
    AX["Codex adapter<br/>instruction template, hook events, transcript format, config"]
  end
  subgraph OUT["Outside Ghost-ALICE, behavior not guaranteed"]
    HC["Host: Claude Code"]
    HX["Host: Codex"]
    BC["Generative backend: Claude"]
    BX["Generative backend: GPT"]
  end
  U["Human user: objective and authority"]
  AD["Add-on"]
  C -->|translated for Claude Code| AC
  C -->|translated for Codex| AX
  AC -->|installed into| HC
  AX -->|installed into| HX
  HC -->|model requests| BC
  HX -->|model requests through the context gateway| BX
  U -->|input| HC
  U -->|input| HX
  C -.->|Stop notice anyone can read| AD
```

Core and the adapters are Ghost-ALICE; the hosts and the generative backends are not. Ghost-ALICE therefore cannot guarantee how either one behaves; it instructs and records within the limits they expose. The user and the add-ons also stand outside. Core code's context gateway runs only on macOS. After the user registers it with the [registration command](docs/getting-started/installation.md#python-contract), it relays Codex's model requests; without that registration, Codex sends them directly to the generative backend. Host formats that still sit in Core code, such as Codex configuration, Claude tool names and the Codex patch format, belong to the adapters. The wiki's [design principles: structure boundaries](https://github.com/AidALL/ghost-alice/wiki/Design-Principles#structure-boundaries) explains why each boundary sits where it does.

## Responsibilities and flow

While a request runs, each participant has one responsibility.

| Participant | Responsibility |
| --- | --- |
| Human user | Supplies the objective, constraints and authority. |
| Response model | Follows Core's instructions: on each input, interprets what the human user wants, selects and performs work, compares claims with originals, then judges the complete answer. |
| Adapter | Translates Core's instructions, hook events, transcripts and configuration for its host. |
| Host | Executes tools, enforces its permission rules, constructs model requests and displays answers. |
| Core code | Binds work, sources and both judgments to the current input that Core code recorded (on Claude Code, an input whose transcript row carries the host's human-origin markers; on Codex, every prompt that Codex's prompt-submit event (`UserPromptSubmit`) delivers), and records and authenticates them. |

```mermaid
flowchart TD
  U["Human user's purpose and authority"] -->|"Input as written (user-said)"| I["Response model: interpret the input (interpreted-as):<br/>objective, constraints and done conditions"]
  I -->|"Work that needs tools, except a local read-only lookup:<br/>new or changed interpretation, before the next tool call"| L["Core code: record the interpretation in the intent ledger"]
  I -->|Interpretation and its assumptions| Q{"Does the human user's input leave more than one reasonable reading<br/>that changes the next action, or a decision only the human user can make?"}
  Q -->|Yes| K["Response model: end the answer with a question that states<br/>the assumption and what changes if it is wrong"]
  K -->|Confirmation question| U
  Q -->|"No; what the human user wants is clear"| M["Response model: select and perform work"]
  M -->|Tool requests the response model sends within what the human user's instructions authorize| T["Host: tools and its permission rules"]
  T -->|Observed results| M
  O["Original evidence"] --> J["Compare claims with originals"]
  M --> J
  J --> W["Judge the complete answer against the input as is and the interpreted<br/>objective, constraints and done conditions"]
  I -->|"Input as is and interpreted objective, constraints and done conditions"| W
  W -->|Correction or needed investigation within what the human user currently requests| M
  W --> R{"Completion claims or an authenticated review the human user requested"}
  R -->|Required| C["Core code: authenticate both judgments and source bindings"]
  R -->|Routine explanation| D["Response model: submit the answer"]
  C -->|Status and references| D
  D --> H["Native host: display the answer"]
  H -->|Stop event| SR["Core Stop script: compare the answer with its recorded review<br/>and record this Stop's notice message in stop-notice.json"]
  SR --> S{"Does the message report an unsupported claim, an issue found in the check,<br/>an unresolved criterion or declared remaining work?"}
  S -->|Yes| N["Core Stop script: return the notice text as systemMessage;<br/>the host shows it after the answer"]
  S -->|No| E["Native host: stop"]
  N --> E
```

The model chooses the next judgment target: micro for an individual result, meso for a subtask or mapping, macro for the integrated outcome and dependencies, or meta for necessity or premise. The task complexity level follows from the work's verification burden; whether the evidence supports full or partial completion is a separate judgment. A mismatch starts investigation; it does not identify the cause. Unresolved conditions prevent their dependent claims or actions while independent work that the human user's instructions authorize can continue. The wiki page [Verification and Evidence](https://github.com/AidALL/ghost-alice/wiki/Verification-and-Evidence) describes these decisions.

## Hook calls

The native host runs the registered command of each Core hook and add-on hook through the hook runner, `_shared/hook_profile_gate.py`, which writes each call to the hook log `~/.ghost-alice/session-logs/<host>/<session>/strict-hook-output.jsonl`. When that write fails, the runner records the failure in the retention failure record `~/.ghost-alice/state-retention-failures.jsonl` when it can, and passes the hook's output to the host as usual. Each registered command carries an installation binding, the value that names the installation's locations, including its ledger root. The figure follows one call of an enabled Core hook whose binding is valid. For an add-on hook, the runner takes the same steps and runs the add-on's own script, which is outside Ghost-ALICE, in place of the Core hook script.

```mermaid
sequenceDiagram
  participant H as Native host
  box Ghost-ALICE
    participant R as Hook runner
    participant C as Core hook script
    participant L as Hook log
  end
  participant M as Response model

  Note over H: A registered event occurs
  H->>R: Run the registered hook command with the event JSON
  R->>R: Check the hook command: no command substitution, newline<br/>or shell control operator, an allowed program, and a ledger root<br/>equal to the one the installation binding names
  break The command check fails
    R->>L: The refusal and its reason
    R-->>H: Exit code 126 without running the hook script
  end
  opt Claude Code PreToolUse or Stop
    R->>R: Record in the intent ledger the inputs whose transcript rows carry<br/>the host's human-origin markers, from the Claude Code transcript<br/>A failure is recorded in the ledger, or else in the hook log, and the hook still runs
  end
  opt Stop
    R->>R: Before an add-on Stop hook, build this Stop's notice message and record it<br/>in stop-notice.json in the session folder unless the file already holds it.<br/>For every Stop hook, set GHOST_ALICE_STOP_NOTICE_FILE to the file's full path.<br/>A failure is recorded in the hook log, and the hook runs without the variable
  end
  R->>C: Run the Core hook script with the event JSON
  opt The script needs a ledger record or a source binding check
    C->>C: Read or write the intent ledger,<br/>or check a source's binding to the current input that Core code recorded
  end
  opt The Core Stop script
    C->>C: Compare the answer with its recorded review, record this Stop's notice message<br/>in stop-notice.json, and return its text as systemMessage when its state is notice
  end
  C-->>R: Output, diagnostics and exit code
  R->>R: Keep only the fields the host accepts<br/>Remove block, stop and permission-decision fields<br/>Report block, stop, deny and ask in systemMessage
  R->>R: Apply the agent visibility profile to the user-facing output
  opt Claude Code PreToolUse or Stop, the runner could not find the host's human-origin markers on an input's transcript row, has not warned about that cause in this session, and the output can carry the warning
    R->>R: Add a systemMessage warning that the response model's interpreted-as record,<br/>response-route and completion records wait for the next input<br/>whose transcript row carries the host's human-origin markers
  end
  R->>L: The hook's original output, diagnostics and exit code,<br/>the visibility decision and the user-facing output
  R-->>H: Output for the host and an exit code<br/>(exit code 2 becomes 1, and a command registered with || true returns 0)
  H->>H: Read the fields its return protocol supports<br/>from the hook response
  opt The response carries systemMessage, and the host supports it for this event
    H->>H: Show systemMessage to the user
  end
  opt The response carries a context field the host supports for this event
    H->>H: Add the context to the model request
  end
  opt The host invokes the response model
    H->>M: Model input assembled for this invocation
    M-->>H: Answer or tool call request
  end
```

On Stop, Core code's Stop script compares the answer with its recorded review and builds one Stop notice message for the current input that Core code recorded and the answer. It records the message at the path the runner sets in the environment variable `GHOST_ALICE_STOP_NOTICE_FILE`: `stop-notice.json` in the session folder `~/.ghost-alice/session-logs/<host>/<session>/`, next to the hook log. The script returns `continue: true`. The message's `state` is `notice` when it reports at least one of four things: a claim the recorded review does not support; an issue found while checking the answer; a criterion a partial-completion block leaves unresolved; or remaining work the response model declared in the route. The script then also returns the notice text as `systemMessage`, and the host shows it after the answer. For an answer with none of the four, the message's `state` is `quiet` and the script returns no `systemMessage`. A plain explanation without a completion block or declared remaining work, whose input has no recorded review, is such an answer. When no completion block stands before a code fence that never closes, a sample block inside that fence counts as a completion block, so that answer gets a notice. As remaining work, the script lists only the rows the response model declared in the route registered for the current input that Core code recorded; a criterion named by no declared row does not appear. The notice's fixed words are English. Claims, unresolved reasons, and each declared row's reason and next needed information appear as the response model wrote them; a claim shows its first 160 characters. An example of the text:

```text
Ghost-ALICE Stop notice: the recorded review does not support 1 of 2 claims.
- Not supported (criterion c2): the report was checked
- Remaining work: unknown; the response model made no declaration for this input
```

Before an add-on Stop hook runs, the runner builds this Stop's message with the same Core code and records it in the file unless the file already holds the same message. The runner then runs the add-on's script with the file's full path in `GHOST_ALICE_STOP_NOTICE_FILE`. The host does not order the Stop hooks of one event; Claude Code runs them in parallel. Core code's Stop script and the add-on hook's runner build the message from the same Stop event JSON and ledger. When that JSON carries the answer text, the two messages are the same: whichever runs first records the message, and the other finds it in the file and leaves the file unchanged. The add-on installer registers add-on hooks only for PostToolUse and SessionStart, so an installation has no add-on Stop hook.

```mermaid
sequenceDiagram
  participant H as Native host
  box Ghost-ALICE
    participant RC as Hook runner for the Core Stop hook
    participant CS as Core Stop script
    participant F as stop-notice.json
    participant RA as Hook runner for an add-on Stop hook
  end
  participant A as Add-on script
  H->>RC: Stop event JSON
  H->>RA: The same Stop event JSON, in parallel
  RC->>CS: Run the script with the file's full path<br/>in the environment variable GHOST_ALICE_STOP_NOTICE_FILE
  CS->>F: Build this Stop's message, then record it unless the file already holds it
  CS-->>RC: continue true, and the notice text as systemMessage<br/>when the message's state is notice
  RC-->>H: continue true, and that systemMessage when the script returned one
  RA->>F: Build this Stop's message, then record it unless the file already holds it
  RA->>A: Run the add-on script with the file's full path<br/>in the environment variable GHOST_ALICE_STOP_NOTICE_FILE
  A->>F: Read the message
```

## Response boundary

The response model performs the work that the human user's instructions already authorize before final delivery. At the response boundary, the response model asks whether a currently actionable required follow-up remains. No means an answer with the supported result and any unresolved conditions. Yes means that answer plus recommended additional work, its reason and the next needed information. Core code can authenticate the remaining work that the response model declared but does not schedule a new request or return from answer delivery to work selection. No actionable follow-up does not establish whole completion: required conditions may still depend on a decision only the human user can make or on external support.

```mermaid
flowchart TD
  R["The response model performs the work that the human user's instructions authorize and assesses the results"] --> N{"Currently actionable required follow-up?"}
  N -->|No| A["Answer: supported result and unresolved conditions"]
  N -->|Yes| B["Answer plus recommended additional work"]
  A --> D["Native host displays the answer. When the Stop notice message's state is notice,<br/>the Core Stop script returns the notice text and the host shows it. The host stops"]
  B --> D
```

A later prompt that the host delivers under the user role starts the next model invocation; Core code schedules none. In that invocation, the response model rechecks the current objective, original criteria and constraints before selecting work.

## Context and original evidence

Use existing originals through their source locations. Preserve a separate body only when a required comparison or recovery depends on it and its original may change or disappear. Long passages, answers and tool results are retrieved when their contents are needed. Ghost-ALICE selects information for the recipient's current decision. Once that decision is made, later handoffs carry the current conclusion, uncertainty, unresolved work and source locations. Current summaries replace obsolete summaries. The audit history stays in separate records for the period each record's rule sets: [Runtime State Retention](docs/reference/repository-structure.md#runtime-state-retention) lists the rules.

```mermaid
flowchart LR
  U["Purpose and unresolved conditions"] --> D["Select information"]
  O["Original sources"] -->|Needed content| D
  R["Conclusions and source references"] --> D
  D --> M["Response model: compare and judge"]
```

Healthy hooks supply current receipts and changed decision-relevant conditions. The common contract and owning skills supply procedures; hooks do not repeat their bodies. Review preparation and recording return references rather than echoing the submitted answer. The complete answer remains available for an explicit later comparison.

The native host independently retains conversation history and builds model requests. Selecting a concise Ghost-ALICE handoff does not remove earlier native messages. Prompt caching, compaction and lower token counts do not by themselves demonstrate that obsolete source bodies have been excluded. Usage observations and native display checks establish only what their original records show.

The context gateway applies supported explicit content choices at the server boundary. When actual continued work requires changing the input representation of used material, the model compares that material with the next decision. It does not predict a lifetime or add a judgment call to every lookup. Core code authenticates the native session, current input and original material independently of work authority. The gateway substitutes explicitly judged bodies with conclusions, unresolved conditions and an original retrieval reference when the protocol supports that change. It preserves needed or unjudged general source bodies and sends server answers unchanged. Original history remains available for explicit retrieval.

The [native context procedure](_shared/context_lifecycle.md#apply-and-observe) owns when the gateway may substitute a body in the request and when it may ask the model server to clear whole tool results. The [session gate notes](docs/policies/session-gate-matrix.md#notes) list source edits, installed bytes and native execution as separate claims.

The gateway records why it leaves a request unchanged: no authenticated supported input change, a failed read, or an unexpected processing error. In each case it forwards the original request. Selection only removes material, so a selection the gateway cannot complete never stops transmission. A request from outside the installation scope or for an endpoint without reduction support also goes upstream unchanged. The gateway returns HTTP 404 for a request whose provider path is not in its configuration. The gateway lets its HTTP transport, Python's `urllib`, follow a redirect from the model server whose target stays in the provider's origin and is unambiguous, under `urllib`'s rules for each status and method, and sends the native client's headers and credentials only within that origin. When `urllib` does not follow such a redirect, as for status 307 or 308 to a POST request, the gateway passes the redirect to the native client with its location resolved to an absolute URL against the URL of the request that received it, so the client cannot resolve it against the loopback address. When a redirect target leaves the provider's origin, switches from HTTPS to HTTP or is ambiguous, or the gateway cannot reach the model server, the gateway returns HTTP 502 with the reason in the provider's error format; it neither follows that redirect nor passes it to the native client. Successful upstream response bytes reach the native client unchanged. launchd, the macOS service manager, does not restart the gateway service after it exits, so requests sent through the gateway fail until the service starts again. When the gateway cannot save a request's usage record to the usage-record file, it records that failure in the retention failure record when it can, and forwards the request.

Installation preserves the listener binding, confirms service shutdown before replacing files and enables the provider after readiness. A failed candidate is recovered from preserved artifacts and service state.

```mermaid
flowchart LR
  P["Retrieval purpose"] --> M["Model: read and judge remaining need"]
  M --> C["Core code: authenticate the post-use judgment"]
  H["Native history"] --> A["Context gateway"]
  C --> A
  A -->|Supported selected input| S["Model server"]
  A -->|No authenticated supported change, or a read or processing failure: original request, with the reason recorded by the gateway| S
  S --> D["Native answer display"]
  O["Original locations"] -->|Explicit retrieval| M
```

## Evidence and native delivery

The model makes two distinct judgments: each claim against original evidence, and the complete answer against the human user's objective and constraints. Core code authenticates ownership, input bindings and source integrity; IDs and hashes do not determine semantic truth. The native host displays the model's answer. Core code's Stop script compares the answer with the recorded review; it does not generate missing judgments or schedule another task.

The [verification procedure](coding-convention/verification-before-completion/SKILL.md) owns evidence selection and completion claims. Its [response review procedure](coding-convention/verification-before-completion/references/response-review.md#prepare-and-record-the-response-review) owns the recording handoff. Other documents refer to that procedure rather than duplicating its commands.

Supported results retain their original evidence, verification times and correction links. A changed relevant target or criterion, a contrary observation, a recheck that the human user explicitly requested or a materially broader claim reopens only the affected evidence. A recording or delivery defect does not justify replaying unchanged business work. The response model may deliver an accurate partial answer as its answer while required work remains incomplete. The wiki page [Focus and Recovery](https://github.com/AidALL/ghost-alice/wiki/Focus-and-Recovery) describes unresolved conditions and the model's next judgment.

## Necessary-work declaration

The response model declares current necessary unprocessed work, its reason and the next needed information in the route that Core code stores. Core code authenticates the selected criteria, the current input that Core code recorded, declared boundary and original-source references without copying their bodies. When originals change, Core code holds only the dependent items of the remaining work that the response model declared while preserving work and proof. Core code records this declaration and schedules none of the work it names; only a later prompt that the host delivers under the user role starts that work. Core code's Stop script writes the declared rows into the Stop notice message; the host shows them in the notice text, and anyone can read them in `stop-notice.json`.
