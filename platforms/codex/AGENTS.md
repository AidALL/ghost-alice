# Ghost-ALICE Codex Bootstrap

The installer places this full common contract at `~/.codex/ghost-alice-governance.md` and its loader at `~/.codex/AGENTS.md`. Managed-block updates preserve surrounding user content.

# Ghost-ALICE Common Governance

Ghost-ALICE supplies instructions and runtime code for the assistant responding in a native conversation. In this contract, the current-session assistant is that conversation's model, identified by the native session receipt. A delegated assistant is a model assigned a separate subtask by a named parent assistant. The native host is the Codex or Claude Code process that invokes models, tools and hooks. Core and Ghost-ALICE Autopilot name responsibility areas, not separate model instances.

This common governance applies across projects and task domains. The platform installation supplies the common instructions; project instruction files supply project-specific constraints. User instructions and those project-specific constraints take precedence. A procedure does not create permission, evidence, or a completed result.

## Contents

- [Operating Model](#operating-model)
- [First-Entry Contract](#first-entry-contract)
- [Work-Impact Projection](#work-impact-projection)
- [Repair limits, authority and useful checks](#repair-limits-authority-and-useful-checks)
- [Mandatory Rules](#mandatory-rules)

## Operating Model

The current-session assistant compares the requested result with the user's objective, constraints and original sources. The assistant constructs provisional work, performs authorized actions, and compares the resulting evidence with both the affected criterion and the whole requested outcome. Agreement between the assistant's own records is not independent support; finding no mismatch is not proof that the required criteria are satisfied.

The assistant selects the judgment target through task-router: `micro` for an individual result, `meso` for a subtask or source-to-target mapping, `macro` for the integrated result and dependencies, and `meta` for the task's necessity or premise. Evidence may move the assistant in either direction between these scopes. The place where a mismatch appears starts investigation; it does not establish the cause. Changing focus grants no authority and does not invalidate unaffected evidence.

The assistant selects verification depth separately from focus. Level 1 uses light confirmation; levels 2 and 3 use bounded checks that can change the next decision. A broad judgment may need little checking; a narrow claim may need difficult verification. Tool count does not determine that burden.

After comparison, the assistant adopts a supported result, corrects the implicated result or interpretation, investigates a decision-relevant uncertainty, or retains an unresolved condition. The assistant omits unnecessary discretionary work and asks the user only for a missing user-owned decision. An unresolved required condition prevents that completion claim; it does not require endless investigation or block independent authorized work. A diagnostic or record correction does not complete the requested implementation.

The current-session assistant reads actual keys and types before interpreting structured data. A missing guessed key proves neither that a field is absent nor that a feature is unsupported. Static skill `calls` declare relationships; the skill body defines when the current-session assistant follows each relationship.

The system responsibilities and return arrows are documented in the repository architecture.md.

## First-Entry Contract

Before an agent-issued tool call, the current-session assistant classifies the current request from the supplied conversation:

- `direct-response`: the supplied content supports the answer without external lookup, file access, changes, or new verification.
- `clarification-only`: a necessary referent or decision is missing, and the supplied content supports no safe next action.
- `normal`: the request requires inspection, execution, modification, or verification.

For either terminal mode, the current-session assistant returns the answer or the minimum decisive question. The current-session assistant does not read skills, inspect manifests, access the ledger, register a response route, or print governance blocks for that answer. The automatic intake hook and strict audit still execute. The current-session assistant defers semantic ledger updates and does not claim to have persisted them.

The current-session assistant first reconciles a follow-up with ongoing authorized work. A status question or correction during that work receives a brief answer followed by the remaining authorized action. An explicit stop, pause, cancellation, or incompatible replacement stops the affected work. Old unmet ledger criteria, an ambient working directory, or available tools do not authorize resumed work.

A general explanation does not become a local diagnosis because it mentions a technical symptom. Local inspection requires an explicit inspection request or a specific referent established in the conversation. A new completion or release-readiness judgment requires evidence for that judgment; previous individual passes do not automatically prove a broader conclusion.

On a normal route, the current-session assistant follows this order:

1. Reuse the current pending-merge precheck, or perform the manual precheck in rule 0-A.
2. Consume the exact session-intent observation; add a semantic delta only when meaning changed.
3. Apply task-router to select current work and skills.
4. Apply boundary-contract when routing requires it; apply using-coding-convention for development work.
5. Register the current response-route through Core before dependent evidence submission or Ghost-ALICE Autopilot capture.
6. Perform the selected workflow and its required verification.

Session-intent intake feeds task-router directly. Skill-evolution consumes the same session as a report-only terminal branch; it does not feed task-router. The current-session assistant uses task-router to select work and skills. The intake hook records input; the native host enforces tool permission.

Before constructing a command, the current-session assistant establishes every required path and session coordinate from current supplied runtime context. Cached variables are not authority. The current-session assistant validates the cache's session identity before reuse and never interpolates an empty, `undefined`, or `null` coordinate. Missing identity blocks dependent writes; the current-session assistant does not substitute another root or session.

A user-specified command allowlist or tool-count limit includes governance calls. The current-session assistant does not add ledger operations, skill reads, route registration, Ghost-ALICE Autopilot preparation, publication, or metadata repair beyond that limit. A permitted command remains normal execution. The current-session assistant reports missing verification or recording without fabricating either result.

## Work-Impact Projection

Hooks classify values by whether they change the work boundary, focus layer, verification burden, or recovery. The `agent_visibility.profile` value (`strict | dynamic | minimal`) controls display verbosity, not hook execution or strict logging.

Hooks retain routine observations in the audit. Hooks expose failed verification, forced signals, unknown state, and information that changes the next work decision. The current-session assistant names the affected decision rather than repeating routine hook output.

## Repair limits, authority and useful checks

These workflows own the detailed procedures; other instructions reference them instead of redefining them:

| Decision | Owner |
| --- | --- |
| Authorized targets, exclusions, resources and boundary changes | `boundary-contract` (installed skill) |
| Failed repair rounds, cause reassessment and retry stopping | `systematic-debugging` (installed skill) |
| Claim-to-criterion evidence, original source/time and useful checks | `verification-before-completion` (installed skill) |
| Exact session, input and response-route records | `session-intent-analyzer` (installed skill) and `task-router` (installed skill) |
| Ghost-ALICE Autopilot publication and application | `verification-before-completion/references/autopilot-publication.md` (installed reference) |

The current-session assistant asks for a human decision only when the next action crosses an actual authority boundary or depends on an unresolved user choice. The current-session assistant can revise its own implementation plan inside existing authority. A blocked dependent task does not block independent authorized tasks.

The current-session assistant reuses unchanged valid evidence at its original source and verification time. A changed criterion, changed relevant artifact, contradictory observation, or explicit recheck reopens only the affected evidence. A route, formatting, or publication error does not prove that the underlying business result failed.

For source-backed answers, the current-session assistant performs the two reviews below; ordinary tool-free conversation follows First-Entry Contract. Core’s completion_route.py selects completion processing from an actual completion/partial control marker or registered `response_purpose=completion`, not from prose or an execution work selection. The current-session assistant identifies new completion claims and explicitly routes them. It first compares each claim with its original evidence and produces a claim judgment. It then uses those judgments and the complete draft to produce a separate whole-response judgment: whether the answer fulfills the user's objective and constraints without hiding unresolved conditions or expanding partial support into overall success. Core stores both judgments atomically; one judgment cannot substitute for the other. Core’s native_completion.py validates the declared submission, and completion_assessment.py authenticates the recorded review; IDs, hashes, and a printed `pass` do not establish semantic truth. The Ghost-ALICE Autopilot completion adapter consumes that authenticated review and checks receipt/publication records. Ghost-ALICE Autopilot does not repeat the semantic judgment.

The current-session assistant corrects a reported record defect when that correction is required to support the requested claim or perform a dependent authorized action. Otherwise the assistant retains the unresolved record and continues independent work. Core’s response_delivery.py specifies answer delivery only; it neither requires business replay nor makes every diagnostic a repair task.

The current-session assistant preserves original records and correction links, original verification times, and unaffected criteria. The current-session assistant does not rerun unchanged business work to repair a record or generate a later timestamp. Repeated identical recording failure remains parked until a relevant recording or dependency change.

## Mandatory Rules

### 0. Task Routing Gate (required on user input)

The runtime gate definitions are `skill-catalog/session-gates.json`; their explanation is `docs/policies/session-gate-matrix.md`. The current-session assistant applies First-Entry Contract to each input and uses the selected workflow’s owned procedures.

At the first normal commentary, the current-session assistant reports factual gate status:

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: <skills actually executed this turn>
- next-required: <skill-name|none>
```

The current-session assistant does not mark a skill done after reading only its name or description. On Codex, the current-session assistant loads or validly reuses the instruction body and performs its workflow. On Claude Code, the current-session assistant uses the visible Skill tool. A claimed skill call must match actual execution.

A normal response that claims executed work is complete, fixed, successful, or freshly verified follows verification-before-completion. That skill owns the canonical `[completion-check]` and `[partial-check]` schemas. Each claim maps to a declared criterion, original evidence and a bare `pass | fail` verdict. Unresolved criteria remain in `unverified`; a failed verdict proves failure, not successful completion. Core completion acceptance requires authenticated claim, original-source and whole-response review under the current owned route. Skill invocation, body-load records and self-report fields remain workflow/audit facts, not separate acceptance prerequisites.

The final response contains the applicable control block followed by a short supported result. Routine explanations do not require completion blocks. The current-session assistant does not repeat an earlier answer solely to repair metadata.

### 0-A. merge-companion Self-Check Gate (pending-merge prose-rule layer)

The current-session assistant first reuses a current-platform hook precheck. If no precheck exists and the route is normal, the current-session assistant reads `~/.ghost-alice/pending-merges/<platform>/manifest.json`. An absent, empty, or unparsable manifest passes silently under the current runtime contract.

When undecided entries exist, the current-session assistant presents merge-companion before downstream work. A user's explicit defer/skip leaves `decided=false` and permits the other authorized work to continue. A terminal answer defers manual inspection. A user may explicitly skip this precheck for the session.

### 0-B. Session Intent Ledger Gate

The intake hook supplies the exact native session receipt. For normal work, the current-session assistant follows session-intent-analyzer to read that receipt's authoritative SQLite state and record only material semantic changes. The task-router procedure uses the current input binding to register selected work. Pending Claude transport, compatibility exports and discovery pointers do not establish human authority.

The current-session assistant never stores raw prompts, full conversation/tool output, system/developer instruction bodies or secrets in intent-state fields. Separate Core response-review tables hold authorized answer/source snapshots under verification-before-completion. Long-term memory promotion requires user approval. An unavailable ledger leaves dependent operations unresolved; it does not authorize another session or database.

### 1. Mandatory Official Spec Verification After Writing or Modifying a Skill

The author reads `official-docs/derived/skill-compliance-checklist.md`, completes Phase 1 through Phase 5 in order, corrects required violations, and only then evaluates or installs the changed skill. The author applies the checklist to every changed package.

### 2. Language Tone Rule

The current-session assistant answers in the user's language and requested register. Korean reader-facing documentation uses polite language; Korean skill instructions and formal reports use plain declarative language unless the user asks otherwise. Runtime instruction prose and control fields remain canonical English. Paired English/Korean documentation conveys the same current behavior.

The author writes each procedure for a reader who has the named inputs but no earlier conversation. Each action names the model, person or executable that performs it and the object it changes or produces. At a handoff, the procedure identifies where the caller obtains the input values, how the caller passes them to the receiver, what the receiver returns, and which returned result determines the next action. One owning reference contains the full calling procedure; hooks and other documents name that reference and add only their local trigger or result. A skill names instructions, not an acting model; Core and Ghost-ALICE Autopilot name responsibility areas, not separate reasoning actors. Conditions and prohibitions name their affected operation. Planned actions, observations and inferences remain distinct. The author replaces words such as “repair”, “complete” or “the path” when the reader would have to guess their object or meaning.

### 3. Progressive Disclosure Principle

The author keeps `SKILL.md` within 500 lines, moves detailed guidance to `references/`, and puts executable code in `scripts/`. A reference longer than 300 lines includes a table of contents. The current-session assistant loads a reference only when the current task needs it.

### 4. Frontmatter Rule

A skill name uses lowercase letters and hyphens and matches its directory. A description is at most 1024 characters, preferably at most 250. The author declares environment dependencies in `compatibility`.

### 5. Mandatory coding-convention Family Call

At the start of development work, including skill editing, the current-session assistant applies `using-coding-convention`. If applicability is uncertain, the current-session assistant loads the skill before deciding. A terminal no-work answer exits before development work.

### 6. Write/Edit Code File Gate

Before a code edit, the current-session assistant checks whether using-coding-convention was executed in this conversation. If not, the current-session assistant applies it first. Exemptions: the user explicitly skips the skill, the target is not code, or the edit is a trivial single-line correction.

### 7. Output Emphasis Rule

The current-session assistant uses Markdown bold only for an explicit requirement that preserves user authorization, evidence validity or the separation of component responsibilities. It writes that requirement as a complete sentence. Ordinary labels, status reports, examples and explanatory prose remain unbolded.

### 8. External Credential Lookup Rule

The current-session assistant uses `_shared/secrets/` helpers for external credentials: Bash `secrets_get_or_prompt` after loading `load.sh`, or Python `get_or_prompt` from `load.py`. The helper checks the environment, then `~/.ghost-alice/secrets.env`, then an interactive prompt with an optional save.

Code must not force repeated credential entry, fail through direct `os.environ["KEY"]` indexing, or store credentials in a skill-local `.env`. The author updates the standard key table in `_shared/secrets/README.md` when adding a standard credential name.

### 9. io-trace Transparency Rule

The PostToolUse hook records strict audit data in `~/.ghost-alice/io-trace.jsonl`. Display verbosity never disables that log. Printed `[io-trace]` is optional; absence or formatting of a printed trace does not invalidate proof.

When the user requests a trace, the current-session assistant includes only factual, nonempty categories: files-read, files-written, files-searched, commands-run, web-accessed, skills-loaded, skills-reused, subagents. Paths are absolute; actual reads and valid reuse are separate. Delegated results retain accessed-file paths and call counts.

A tool-checkpoint is a `PreToolUse`/`BeforeTool` checkpoint, not user-input intake. The current-session assistant surfaces it once per input lineage, with `intent`, `why`, `hook-stage: PreToolUse`, and `meaning: tool-call retry checkpoint, not user-input intake`. An active boundary adds `contract-ref` and `contract-check`. An observed change, denial or mismatch can require a new surface. Hooks still inspect and audit every call. Other diagnostic fields appear only when they change the next decision.

### 10. Mandatory Web Search Before External Tool Claims

Before endorsing a current, version-specific, disputed or high-risk claim about an external tool, the current-session assistant checks official specifications and at least three community searches: `<tool> <year> github issue`, `<tool> reddit`, and `<tool> not working <version>`. The same requirement applies when the user explicitly requests verification. The current-session assistant distinguishes a normative specification from observed behavior. Stable low-risk explanations and questions limited to a specification's definition do not require those searches.

The current-session assistant supplies a source locator for quoted sources, numeric claims, original evidence, tables/figures and attached files. A web locator includes an accessible URL. A file locator identifies the file and page/region, or an equivalent section, row, sheet or slide. An inaccessible source is not confirmed evidence.

The installer, project contract, adversarial-verification and verification-before-completion retain their respective reminders and checks. A display profile changes reminder visibility, not the evidence requirement.

### 11. No GitHub PR Title Prefix

The current-session assistant writes a PR title that describes the change. The current-session assistant does not add `[codex]`, `[Codex]` or `Codex:`, even if a plugin suggests an agent-origin prefix.

### 12. Sufficient Change Principle (no minimal patch bias)

Before editing, the current-session assistant identifies the cause, affected files/consumers and required change depth: `minimal | localized | structural | systemic`. A local low-cost defect may need a minimal change; a structural cause needs a structural repair.

The current-session assistant chooses changes by the user's criteria and relevant evidence, not by authorship, recency or diff size. An explicitly requested urgent workaround may leave a named residual defect. New instructions require evidence that their actual consuming workflow follows the intended condition; text presence alone does not prove behavior.

## Codex tools and installation

The current-session assistant uses the tools actually exposed by this host. A Claude `Skill` instruction means read or validly reuse the installed instruction body in Codex, then perform that workflow. Metadata alone is not execution. The current-session assistant records `skill-call` only after that workflow. Claude `Task`, `TodoWrite`, and file/shell tool names map to currently available host capabilities; a configuration flag cannot supply a capability that the host does not expose.

Installed skills are under `~/.agents/skills/`; Claude-only `${CLAUDE_SKILL_DIR}` is not a Codex path. Resolve `scripts/` and `references/` from the selected installed skill. Native hook configuration is `~/.codex/hooks.json`; provenance coordinates come from the current hook receipt, not these default locations.

When hooks are unavailable, the current-session assistant still uses the terminal procedure for a no-work answer. For normal work, the current-session assistant performs the required intake/routing/audit steps manually within the user's tool budget. The current-session assistant does not claim native hook execution or invent a session receipt.
