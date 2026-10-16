# Context Selection

Task-router applies this reference when continued work needs to change what already-used material stays in model input, or when a decision needs an original source passage again.

## Select information for the next decision

The responding model chooses the information for its next decision from the current objective, unresolved conditions and original-source locations. Each selected body has a purpose, a consuming decision and a remaining need. Storage preserves an original independently of selection for model input. A source body or tool result enters a decision only when the model needs its contents for a named comparison or operation. Long passages, complete documents and detailed answers remain valid selections when the work requires them; Ghost-ALICE imposes no general output-length budget. Independent required reads can share one native invocation. A new model round must resolve information needed before a dependent action; procedure order alone does not justify repeated sampling.

## Retrieve originals

For local originals, `python3 <installed-shared-directory>/source_lookup.py <path>` returns file metadata. `--find <literal>` returns matching coordinates; `--line <number>` returns the selected line; `--pointer <JSON-pointer>` returns the selected JSON value; `--record <number>` selects a JSONL record. Optional `--offset` and `--length` select a caller-requested part of a string or line. The reader does not shorten a selected value automatically, copy the original or determine whether it proves a claim. For connector tools, select the relevant source items and fields where the interface supports selection. Reuse applicable skill bodies; load a reference when its procedure is required.

## After use

After using a source body or tool result, the responding model updates the current summary with the conclusion, uncertainty, unresolved condition and source locator. Later Ghost-ALICE handoffs pass that current state and locator, without replaying the body. A subsequent decision that needs the original wording retrieves the relevant source again. Strict audit retains the original observation and correction links; neither a summary nor a keyword replaces proof or permission. Core keeps work, authorization and proof records for its own decisions, separately from model input.

## Change the input representation

The responding model selects the next work and the information that work needs together. Native origin does not exempt prior messages from that judgment; the current request and still-needed instructions retain their meaning. When actual continued work requires changing the input representation of already-used material, the model follows the current receipt's `context_selection` procedure. It compares the material with the next decision rather than predicting its future lifetime or adding a separate judgment call to every lookup. A possible later need retains a retrievable original, not automatic reinjection. Core authenticates declared choices, and the gateway applies them; unsupported or unpublished choices leave the body intact. Content-use dispositions authenticate session, input and originals without creating an execution route. Original history and server answers are preserved. Actual request observations establish adoption and substitution.

## Host history, compaction and caching

The native host serializes retained conversation messages and loaded tool definitions into model requests. Ghost-ALICE handoff selection does not remove earlier messages from that native history. Native compaction and prompt caching are separate host functions: caching reuses part of the request, and compaction changes retained history. Neither demonstrates decision-based exclusion of an obsolete source body. Core authenticates original sources separately from their retrieval. Ghost-ALICE does not insert another review-model call or intercept native answer display.
