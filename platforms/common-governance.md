# Ghost-ALICE Common Governance

## Operating Model

The response model compares the requested result with the user's objective, constraints and original sources. It selects micro (individual result), meso (subtask or mapping), macro (integrated result and dependencies), or meta (necessity or premise) as the next judgment target. Verification depth is a separate choice. Contrary evidence or a mismatch can change either choice. Finding no mismatch does not establish completion. An unresolved criterion blocks its dependent claim or action; independent authorized work can continue.

Core and Ghost-ALICE Autopilot are responsibility areas, not model identities. The native host executes tools and enforces permission. The response model performs the authorized work. Core authenticates records and source bindings. Ghost-ALICE Autopilot consumes authenticated results to judge remaining work and request authorized continuation from the native host. A request does not prove that the host or response model executed the work.

User instructions take precedence over this contract. A procedure, routing keyword, stored result or available tool grants no authority. Project instructions add project-specific constraints.

## First-Entry Contract

Classify the input from the supplied conversation before calling tools:

- `direct-response`: the supplied content supports the answer without external lookup or execution.
- `clarification-only`: a missing user-owned decision prevents the next useful action.
- `normal`: the request requires inspection, execution, modification or fresh verification.

For either terminal mode, answer without reading skills, accessing the ledger, registering a route or printing governance blocks. Automatic intake and strict audit still run. A status question does not cancel authorized work; an explicit stop, pause or replacement does.

For normal work, use the intake receipt's exact root, platform, session and input identity. Select the next decision from the supplied request and applicable current intent through `task-router`. Apply `session-intent-analyzer` when stored intent can change that selection or a semantic update is needed; skill-evolution is a report-only branch. Load or reuse only the selected procedure. A required intent lookup uses `session_intent_ledger.py --read-intent`; a required detail uses `--read-state --pointer <JSON-pointer>`. Do not query state merely to acknowledge intake or tool use, and do not print the complete ledger into model context. Core still reads and validates canonical state for dependent writes, owned routes, authority and completion records. Missing or pending identity prevents those dependent operations; never substitute another session or a discovery pointer.

Reuse the native pending-merge precheck as an observation. Apply `merge-companion` when the user requests review of backed-up changes or the selected work can modify, replace or install the affected skills. An unrelated bounded read-only lookup neither inspects the manifest again nor loads the merge procedure. Before affected changes, inspect the current platform's manifest if no precheck exists; an explicit user defer permits other authorized work and leaves the backups undecided. Apply `boundary-contract` when routing requires it, and `using-coding-convention` for development. Register the current Core response-route before dependent evidence or completion recording. Content-use dispositions authenticate session, input and originals without creating an execution route. Every agent call, including governance, ledger access and Ghost-ALICE Autopilot recording, counts against the user's tool limit.

## Context and Retention

The responding model chooses the information for its next decision from the current objective, unresolved conditions and original-source locations. Each selected body has a purpose, a consuming decision and a remaining need. Storage preserves an original independently of selection for model input. A source body or tool result enters a decision only when the model needs its contents for a named comparison or operation. Long passages, complete documents and detailed answers remain valid selections when the work requires them; Ghost-ALICE imposes no general output-length budget. Independent required reads can share one native invocation. A new model round must resolve information needed before a dependent action; procedure order alone does not justify repeated sampling.

Intake records input identity and returns the current receipt. The common contract owns the stable procedure; a healthy intake hook does not repeat that procedure. `--read-intent` returns current routing keywords, a current summary and detail pointers. An intent update replaces the affected current lists and summary instead of appending earlier versions. Omitted fields retain their current value; an empty list removes obsolete entries. Core and Ghost-ALICE Autopilot keep work, authorization and proof records for their own decisions, separately from model input.

For local originals, `python3 <installed-shared-directory>/source_lookup.py <path>` returns file metadata. `--find <literal>` returns matching coordinates; `--line <number>` returns the selected line; `--pointer <JSON-pointer>` returns the selected JSON value; `--record <number>` selects a JSONL record. Optional `--offset` and `--length` select a caller-requested part of a string or line. The reader does not shorten a selected value automatically, copy the original or determine whether it proves a claim. For connector tools, select the relevant source items and fields where the interface supports selection. Reuse applicable skill bodies; load a reference when its procedure is required.

After using a source body or tool result, the responding model updates the current summary with the conclusion, uncertainty, unresolved condition and source locator. Later Ghost-ALICE handoffs pass that current state and locator, without replaying the body. A subsequent decision that needs the original wording retrieves the relevant source again. Strict audit retains the original observation and correction links; neither a summary nor a keyword replaces proof or permission.

The native host serializes retained conversation messages and loaded tool definitions into model requests. Ghost-ALICE handoff selection does not remove earlier messages from that native history. Native compaction and prompt caching are separate host functions: caching reuses part of the request, and compaction changes retained history. Neither demonstrates decision-based exclusion of an obsolete source body. Core authenticates original sources separately from their retrieval. Ghost-ALICE does not insert another review-model call or intercept native answer display.

The responding model selects the next work and the information that work needs together. Native origin does not exempt prior messages from that judgment; the current request and still-needed instructions retain their meaning. When actual continued work requires changing the input representation of already-used material, the model follows the current receipt's `context_selection` procedure. It compares the material with the next decision rather than predicting its future lifetime or adding a separate judgment call to every lookup. A possible later need retains a retrievable original, not automatic reinjection. Core authenticates declared choices, and the gateway applies them; unsupported or unpublished choices leave the body intact. Original history and server answers are preserved. Ghost-ALICE Autopilot decides whether authorized work continues from Core's unresolved work; it does not alter content-selection rules. Actual request observations establish adoption and substitution.

## Compact Instructions

Preserve the current objective, applicable user restrictions, unresolved decisions, next action and exact source locations. Replace obsolete summaries; do not enumerate all past user messages, replay completed steps, or copy tool output and file bodies. Preserve negation and uncertainty. The receiving model retrieves a source passage when the next decision requires it. The native host controls when automatic compaction occurs.

## Evidence and Delivery

The claim reviewer compares each assertion with original evidence. The whole-response reviewer then judges the complete answer against the user's objective and constraints, using the claim judgments and authenticated excerpts. These are separate judgments. Routine explanations use available original passages and locators without separate preparation or recording calls. New completion or partial-completion claims, and authenticated explanation reviews explicitly requested by the user, record both judgments. Core validates record ownership, input binding and source integrity; IDs and hashes do not determine semantic truth. Ghost-ALICE Autopilot does not repeat either semantic judgment.

`verification-before-completion` owns evidence selection, the two judgments, completion controls and publication. Read its publication reference only for the required recording step. The responding model uses its claim judgments and original-source references for the whole-answer review, retrieving only the passages that judgment needs. The native host displays the answer without another model call. Native Stop records assessment outcomes; it does not create missing judgments or request another answer.

Reuse unchanged evidence at its original time and source. A changed relevant target, criterion or contrary observation reopens only the affected check. A recording error does not refute the business result or authorize replay. Preserve original records and correction links, and leave unaffected criteria intact. Source edits, tests, installation, native execution, publication and criterion application are distinct observations.

## Instruction and Implementation Rules

- Name the actor, action, input, output, recipient and condition. Each owning procedure describes its handoff once; other documents refer to it. Write current behavior without historical alternatives. Keep paired English/Korean documents and diagrams aligned with callers.
- Use `systematic-debugging` for the cause and sufficient correction depth; `boundary-contract` owns authority. Do not substitute extra warnings, exceptions or retries for correcting the cause. `verification-before-completion` owns useful checks and stopping unnecessary verification.
- Before editing a skill, follow `official-docs/derived/skill-compliance-checklist.md`, phases 1–5. Keep the skill entry short, detailed procedures in references, and executables in scripts. Load details only for the selected action.
- Use installed `_shared/secrets/` helpers for credentials. Never expose secrets or repurpose native session identity. Explicit paths and the current host receipt identify the installation and owner.
- Answer in the user's language. Korean reader documentation uses polite language; runtime instruction prose and control fields remain canonical English. Use bold only for an explicit authorization, evidence or responsibility invariant. Do not add an agent prefix to GitHub PR titles.

### 9. io-trace Transparency Rule

Hooks record strict audit independently of displayed guidance. Expose a PreToolUse checkpoint when its result changes an authority boundary, the next work decision or a required verification. State `intent`, `why`, `hook-stage: PreToolUse`, and `meaning: tool-call retry checkpoint, not user-input intake`; an active boundary adds `contract-ref` and `contract-check`. Repeated healthy tool calls and new input receipts alone do not require another checkpoint message. Preserve failed, denied and unknown-state observations that affect the next decision. Do not print full audit records. Printed `[io-trace]` is optional and never establishes proof.
