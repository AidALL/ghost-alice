# Ghost-ALICE OS Project

Ghost-ALICE OS is an agent governance operating layer that manages the work boundary, verification, session intent, install state, and platform-specific hook behavior of an AI agent session. It is not a general-purpose operating system or a standalone agent runtime.
## Contents

- [Operating Model](#operating-model)
- [Work-Impact Projection](#work-impact-projection)
- [First-Entry Contract](#first-entry-contract)
- [Mandatory Rules](#mandatory-rules)
  - [0. Task Routing Gate (required on user input)](#0-task-routing-gate-required-on-user-input)
  - [0-A. merge-companion Self-Check Gate (pending-merge prose-rule layer)](#0-a-merge-companion-self-check-gate-pending-merge-prose-rule-layer)
  - [0-B. Session Intent Ledger Gate](#0-b-session-intent-ledger-gate)
  - [1. Mandatory Official Spec Verification After Writing or Modifying a Skill](#1-mandatory-official-spec-verification-after-writing-or-modifying-a-skill)
  - [2. Language Tone Rule](#2-language-tone-rule)
  - [3. Progressive Disclosure Principle](#3-progressive-disclosure-principle)
  - [4. Frontmatter Rule](#4-frontmatter-rule)
  - [5. Mandatory coding-convention Family Call](#5-mandatory-coding-convention-family-call)
  - [6. Write/Edit Code File Gate](#6-writeedit-code-file-gate)
  - [7. Output Emphasis Rule](#7-output-emphasis-rule)
  - [8. External Credential Lookup Rule](#8-external-credential-lookup-rule)
  - [9. io-trace Transparency Rule](#9-io-trace-transparency-rule)
  - [10. Mandatory Web Search Before External Tool Claims](#10-mandatory-web-search-before-external-tool-claims)
  - [11. No GitHub PR Title Prefix](#11-no-github-pr-title-prefix)
  - [12. Sufficient Change Principle (no minimal patch bias)](#12-sufficient-change-principle-no-minimal-patch-bias)


## Operating Model

- Do not conclude a complex task in one pass. Repeatedly compare the current state against the schema, the SSOT, the evidence, and the constraints. When a mismatch appears, fix it or hand it to a human.
- The focus scope is not fixed and does not expand in only one direction. It moves back and forth across micro, meso, macro, and meta according to the mismatch location and the verification burden. When a large premise or logic is wrong, fix the higher scope. When a small work output is wrong, fix that lower unit.
- Do not judge complexity by tool count alone. Look at the verification burden as a whole, including source selection, source-to-target mapping, format and schema constraints, and recovery cost.
- Follow the sufficient change principle. Minimal change is not a golden rule. It is one option chosen when the problem cause, structure, and impact surface are local.
- Handle task-complexity-level-1 work with light confirmation. For task-complexity-level-2 and above, use checkpoint-based re-verification by default, and for task-complexity-level-3, treat the re-verification loop itself as the body of the work.
- After session-intent and downstream gate context are available, task-router emits a reusable routing-surface for work complexity, focus, verification burden, boundary need, and forced visibility. Session intent records semantic facts and decisions; governance surface consumers read the routing-surface instead of creating a competing complexity scale.
- `calls` expresses only static and sparse relationships. Repeated loops such as re-reviewing intermediate state, re-fetching evidence, and fixing mismatches are handled by the body procedure and the runtime.
- For structured data (manifest, JSON, config, schema), do not summarize by guessing field names. Check the keys and types first and summarize only with fields that actually exist. If a guessed field turns out null, that is not evidence of absence; it means you read it wrong, so read the actual schema again.
- For detailed definitions, follow this document's operating model and `architecture.md`.

## Work-Impact Projection

Work-Impact Projection classifies hook-internal values by whether they change
the work boundary, focus layer, verification burden, or recovery path.
Stable contract phrase: change the work boundary, focus layer, verification burden, or recovery.

- Hook execution and the strict audit log are never reduced.
- `agent_visibility.profile` selects the user-screen verbosity only; it does
  not gate hook execution, strict logging, or work-impact classification.
- Forced/risk/gate values and failed verification always break through as
  user surface forced and model hint full.
- Routine/debug values remain full in the strict log, but they do not become a
  separate reasoning-policy axis. They are omitted from model hints unless
  they change focus, boundary, verification, or recovery.
- Unknown, ambiguous, or failed values fail closed to fuller surface and reopen
  focus through the routing/scope-reopen path.
- Goal: only values that can change the next work decision affect focus,
  boundary, verification, or recovery. Token reduction is a consequence, not a
  metric.

## First-Entry Contract

When a user turn begins, apply the execution contract below before any long explanation.

1. Resolve the pending-merge precheck first.
2. Connect the `session-intent-analyzer` intake.
3. Fix the `jailbreak-detector/downstream-gates` state.
4. Run `task-router`.
5. Leave `[gate-state]` in the first commentary.
6. Surface a `[tool-checkpoint]` once for the user-input tool batch. The hook still checks every tool call; repeated calls in the same session input lineage do not repeat the user-facing checkpoint text unless state changes. Always carry at least `intent` and `why` on the surfaced block. Add `procedure` when it changes the next work decision or clarifies a non-routine step. Add `contract-ref` and `contract-check` when a boundary-contract is active. Add `localized-human-note`, `rejected-alternatives`, `unverified-premises`, and `failure-mode-if-wrong` only when a side effect, forced signal, mismatch, or meaningful user decision point makes those fields useful. For a read-only call with no active boundary-contract and no forced signal, `intent` and `why` are sufficient.

`tool-checkpoint` is not user-input intake. It is a tool-stage `PreToolUse`/`BeforeTool` retry checkpoint. Its field tiers follow the observable tool kind and the recorded boundary/forced state, not a self-judgment that the call is safe, and the gate itself is never skipped.

## Mandatory Rules

### 0. Task Routing Gate (required on user input)

The session gate SSOT is `skill-catalog/session-gates.json` and `docs/policies/session-gate-matrix.md`. Every conversation follows that matrix.

This procedure is a quality-maintenance device that the user confirmed across repeated work. Realigning the goal, constraints, output, and verification criteria on every user input preserves the user intent, the work scope, and the verification quality. If the agent skips it based only on a judgment of "a simple follow-up" or "the procedure is excessive", it can lead to stale routing and insufficient verification.

When there is user input: 1. the `session-intent-analyzer` intake is connected first. 2. Then `jailbreak-detector` gets the chance to make a security judgment on the current input, and on a current-lineage block it carries the decision to `downstream-gates.json`. 3. If there is no block gate, treat it as a silent allow and then call the `task-router` skill. 4. task-router must run after the session-intent-analyzer intake and the jailbreak-detector downstream gate, and before downstream work or a tool call. Check its applicability regardless of domain, including coding, documentation, research, and chores.

The normal order is `pending-merge precheck -> session-intent-analyzer -> jailbreak-detector/downstream-gates -> task-router -> downstream/tool-checkpoint`. Describing it as `task-router -> session-intent-analyzer`, or arranging the execution order so that it skips passing through the detector, is a rule violation.
Here `tool-checkpoint` is not a user-input intake step. It is a tool-stage `PreToolUse` checkpoint. When it is surfaced, place `hook-stage: PreToolUse` and `meaning: tool-call retry checkpoint, not user-input intake` together.

task-router scans skill descriptions to match output, verification, and lifecycle skills, records the matching result, and then starts the work. When a skill does not apply, record briefly why it is not a target.

Do not skip this gate based only on the agent's own judgment, such as "already routed on a previous input", "the same domain", "a simple follow-up", or "the user told me to hurry". When there is user input, apply this gate.

Runtime checkpoint: leave the block below in the first commentary.

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: session-intent-analyzer (this turn); task-router (this turn); using-coding-convention (this turn) | n/a
- next-required: <skill-name|none>
```

When task-router outputs `boundary-contract: required`, the next required gate is `boundary-contract`. task-router only judges whether boundary-contract is required, and it does not directly write allowed-surface, filenames, or test-purpose.

Ghost-ALICE OS uses an English canonical narrative + English control surface. Canonical narrative, including philosophy, explanation, operating intent, failure cases, and human-facing prose, is written in English. Korean is a secondary aid for Korean reviewers and contributors, not the main language. Field names, enum values, literal tokens, gate schemas, and allowed/forbidden values stay English and are not translated.

Leave the block below when the final response claims executed work is complete, fixed, successful, or freshly verified. Routine explanations, meta-discussion, and options do not require `[completion-check]` unless they claim finished work or verified results.

```text
[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - <criterion-id>: <user-intent-or-contract-condition> [source: user-explicit | inferred | previous-tool | system-doc]
- claim-evidence-map:
  - claim: <completion-or-recommendation-claim>
    criterion: <criterion-id>
    evidence: <fresh command, inspected file, source locator, or tool output>
    verdict: pass | fail
- unverified:
  - none
- evidence: <fresh command or inspected file>
```

The `acceptance-criteria` are verifiable completion conditions extracted from the user intent and the locked decisions. The `claim-evidence-map` connects each closure claim to the criterion it satisfies and the fresh evidence that satisfies it. If any criterion is `unverified`, do not speak as if complete or successful. State the partial status and the remaining verification instead. A finalized `[completion-check]` allows only `verdict: pass | fail` and `unverified: none`. If there is any unverified item, it is not a finalize, so do not emit a `[completion-check]`. Report the partial status in prose instead. Peripheral evidence such as a link check, lint, or diff check is completion evidence only when it connects directly to that criterion. Installed Stop/AfterAgent completion hooks require `[completion-check]` for executed-work closure claims and allow routine non-closure responses.

The `claim-evidence-map` (each claim bound to fresh evidence and a `pass | fail` verdict, with `unverified: none`) is the always-required honesty core. The separate `acceptance-criteria` enumeration is conditional: when the session-intent ledger records no semantic intent delta for the turn, the `[completion-check]` may use a compact form that omits the `acceptance-criteria` block, since the criterion enumeration would only duplicate the claim-evidence-map. When intent materially changed this turn (the ledger recorded a semantic delta), use the full form with `acceptance-criteria` and bind every claim to a criterion id. This is a content-level reduction driven by the recorded delta, not a self-judgment that verification can be skipped, and the claim-evidence honesty core is never dropped.

Hard sequence: skill load/call -> fresh verification -> [completion-check]. Before claiming executed work is complete, fixed, successful, or freshly verified, load or call `verification-before-completion` for the current turn, run and read the fresh verification, and only then write `[completion-check]` with `skill-call: verification-before-completion (this turn)`. If any step is missing or out of order, the completion-check is invalid.

Final message surface contract: when a final user surface includes an executed-work closure claim or explicit `[completion-check]`, close in the order `[completion-check]` -> a short summary -> `[io-trace]`. Otherwise, routine explanations can close with a short summary and `[io-trace]` only. Use `[gate-state]` only on the opening surface early in the turn, and do not attach it again after `[completion-check]`. Keep the summary short, with only the conclusion and the key evidence. Tool calls (Bash, Read, and so on) appearing folded in the UI are not a defect, so do not do separate work to reduce raw command exposure. The target of surface brevity is the final user commentary, not the folded tool blocks.

The `skill-call:` line is a factual record that the relevant skill workflow was actually performed in the current turn through that platform's skill execution mechanism. On a platform with a visible Skill call surface, such as Claude Code, record it only after the actual call. On a platform without a visible Skill tool, such as Codex, record it only when the skill's `SKILL.md` was actually read and the procedure was followed.

Points to follow in an environment without a Codex visible Skill surface:
- Always read the skill's `SKILL.md` before marking a required gate as complete.
- Do not treat a gate as complete based only on metadata, description, memory, a prior turn, or an "already know it" reason.
- If you did not read `SKILL.md` in the current turn, do not list that skill in `skill-call:`. That gate is still pending.
- Apply the same standard to simple tasks, already-routed tasks, and tasks where the metadata looks sufficient.

### 0-A. merge-companion Self-Check Gate (pending-merge prose-rule layer)

Immediately after every user input, and before the session-intent-analyzer intake and rule 0 (the task-router call), check the following.

1. Identify the current platform:
   - Claude Code: `claude`
   - Codex: `codex`
2. Check whether the current turn or the session-start hook output contains a current platform pending-merge precheck result.
3. If the hook reported an undecided entry, surface `merge-companion` first and show the status and options to the user. If the user explicitly defers or skips, that pending merge can be left as `decided=false` while returning to rule 0 (task-router).
4. If the hook issued no pending warning and provided a contract that it performed the current platform precheck, record `merge-companion-precheck: clean (hook-verified)` and do not run an additional shell manifest check.
5. If there is no hook evidence or the environment is hookless or manual, inspect the `~/.ghost-alice/pending-merges/<current platform>/manifest.json` file directly.
6. On direct inspection, if there is an undecided entry, surface `merge-companion` first. An explicit user defer or skip can pass through, and if the manifest itself is absent, `entries` is empty, or JSON parsing fails, it passes (omit even a one-line notice, a silent clean pass).

This gate is checked before task-router. Because pending-merge handling is a user-asset protection area, it is looked at before general routing. Looking first means looking first; it does not mean forcing a merge or discard decision.

Runtime checkpoint: record the 0-A check result in the [gate-state] block of the first commentary.

```text
[gate-state]
- merge-companion-precheck: pending=N | clean | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: ...
- next-required: <skill-name|none>
```

Exemption: when the user explicitly instructs to "skip the merge-companion self-check" for this session only.

This rule is the document layer of the pending-merge layered gate. It operates together with the SessionStart hook (session-start layer) and the UserPromptSubmit hook payload (user-prompt layer). If the hook already performed the current platform precheck, reuse that result, and if the hook failed or the environment is hookless, check directly.


First-entry intake invariant:
- Every user input is connected to the session-intent intake path first.
- The mandatory main path after pending-merge precheck is not a single line through skill-evolution. session-intent-analyzer fans out to skill-evolution(report-only terminal branch) and jailbreak-detector; task-router runs only after jailbreak-detector opens downstream gate.
- `skill-evolution` is a report-only terminal branch from `session-intent-analyzer` and does not feed `task-router`.
- `task-router` is a consumer of `session-intent-analyzer` and `jailbreak-detector/downstream-gates.json` context and an agent-side request decomposition step. It does not own user-input intake, raw intent inference, ledger updates, jailbreak decisions, downstream gate state, or tool permission.
- The task-router reminder hook withholds task-router until session-intent preflight exists and jailbreak-detector has had the first chance to record a current-lineage block. If no current block gate exists, absent `downstream-gates.json` is silent allow; after release, task-router reads the session intent ledger, performs atomic meaning decomposition, and assigns skills.
- Missing `current-session.json`, `intent-state.json`, hook payload, preflight evidence, or semantic delta evidence is not a deny reason for first entry.
- Before the first tool call, the session-intent intake and bootstrap must be connected first, and the absence of evidence itself is not a tool-checkpoint block reason.
- Missing session-intent evidence means intake/bootstrap must run or continue; it does not mean tool-checkpoint may infer risk from absence.
- tool-checkpoint must not predict the user's next input or treat unknown intent as a block condition before intake.

### 0-B. Session Intent Ledger Gate

On every user input, `session-intent-analyzer` updates the intake of the per-session intent ledger. On a platform where the hook fires, the UserPromptSubmit/BeforeAgent hook does not store the raw prompt. It leaves `input_digest`, `input_char_count`, `intake_status=observed`, and `intent_delta_status=not-provided`, and it updates the `.tmp/session-intent/<platform>/current-session.json` pointer at the Ghost-ALICE repository root. The agent augments a compressed delta in the `intent-state.json` of the same session id only when the user's goals, constraints, decisions, non-goals, open questions, or completion criteria materially change. `intent-state.json` is update-plus-accumulate state. Scalar intent such as `current_goal` and `user_intent_summary` is corrected to the latest delta when there is a semantic delta, and list information such as constraints, non-goals, open questions, criteria, and decisions accumulates by deduplication or merge by id.

The hook's digest-only observation is evidence of intake completion. A semantic delta is required only when the user's intent, constraints, decisions, or completion criteria change. If no delta is needed, you can leave the `last_semantic_delta_status=not-provided` state as is and mark `session-intent-analyzer: done`. If a delta is needed but was not recorded, it is `hook-observed`.

The consumers of this ledger are `skill-evolution` and `jailbreak-detector`. `skill-evolution` interprets the tool sequence together with the change in user intent, and `jailbreak-detector` compares the current input summary against the accumulated intent and constraints. The deterministic hard-block rule is a narrow regression guard for explicit, high-confidence attack signals, and it is not a proof that every jailbreak is blocked. A gate block derives only from the meaning judgment that the model recorded (`model_security_decision`). Progressive jailbreak resistance across multiple turns depends on the quality of the intent summary, correction, and accumulation in `session-intent-analyzer` and on the quality of the accumulated-constraint comparison in `jailbreak-detector`.

The pending-merge precheck is a pre-routing and session-start layer that completes before the user-input governance graph begins. After this precheck is clean or surfacing ends through an explicit user defer or skip, the runtime hook graph fixes the session intent ledger state first. User input -> the `session-intent-analyzer` hook records the digest, the session ledger, and the `current-session.json` pointer and allows -> `skill-evolution` and `jailbreak-detector` consume the same session temp files -> `skill-evolution` terminates as a report-only branch -> `jailbreak-detector` records `model_security_decision` in the ledger and carries only a current-lineage block to `downstream-gates.json` -> the task-router reminder hook confirms the session-intent preflight and the absence of a current-lineage block and releases with a silent allow -> `task-router` reads the session-intent ledger and performs only atomic meaning decomposition and the routing decision -> the tool-stage `tool-checkpoint` looks at the current-lineage block gate. If `opened=false` or `decision=block`, it denies. An absent gate or any other state is a silent allow. `tool-checkpoint` does not use tool-call identity, payload content, or audit, log, and correlation metadata as decision input. Audit, log, and correlation metadata stay outside the decision body. `tool-checkpoint` is a `PreToolUse` checkpoint and is not user-input intake.

Prohibited:
- storing the raw prompt
- storing the full conversation or tool output
- storing the original system or developer instructions
- storing raw secret, token, API key, password, or private key values
- promoting long-term memory without user approval

In a hookless or manual environment, apply this contract manually before the first response and record the `session-intent-analyzer` status in `[gate-state]`.

### 1. Mandatory Official Spec Verification After Writing or Modifying a Skill

After writing a new skill or modifying an existing skill, you must pass the process below. Complete this verification before moving on to the testing or evaluation stage.

Verification process:

1. Read `official-docs/derived/skill-compliance-checklist.md`.
2. Run Phase 1 through Phase 5 of the checklist in order.
3. If there is a violating item, fix it and re-verify.
4. Proceed to the next stage (testing, evaluation, deployment) only after every item passes.

### 2. Language Tone Rule

The response language follows the user's input language. Answer in English for English input and in Japanese for Japanese input. Apply the tone rule below only when writing Korean outputs, such as proposals, official letters, government project documents, and Korean reports.

Korean outputs unify to the plain declarative style.

- Honorific endings are prohibited.
- Casual banmal endings are prohibited.
- English skills, English outputs, and other-language outputs are not subject to this rule and use that language's formal register.

### 3. Progressive Disclosure Principle

- SKILL.md: within 500 lines, core guidance only
- references/: detailed reference documents (loaded when needed)
- scripts/: executable code
- a reference file over 300 lines must include a TOC

### 4. Frontmatter Rule

- `name`: lowercase and hyphens, must match the directory name
- `description`: 250 characters or fewer recommended, 1024 characters or fewer required
- `compatibility`: state it when there is an environment dependency

### 5. Mandatory coding-convention Family Call

In every conversation that begins coding or development work, including writing or modifying a skill, call the `coding-convention/using-coding-convention` entry point first. Call it if there is even a one percent chance it applies. If it does not fit the situation after the call, drop it then.

Family composition (14 sub-skills):

- Process: brainstorming, writing-plans, systematic-debugging, verification-before-completion, dispatching-parallel-agents
- Implementation: test-driven-development, subagent-driven-development, using-git-worktrees, executing-plans, finishing-a-development-branch
- Review and meta: requesting-code-review, receiving-code-review, writing-skills, using-coding-convention

### 6. Write/Edit Code File Gate

Immediately before calling Write or Edit on a code file (.py, .js, .ts, .jsx, .tsx, .java, .go, .rs, .c, .cpp, .cs, .rb, .sh, .bat, .ps1, and so on), check whether `using-coding-convention` has been called in the current conversation.

Gate rule:

- call history exists -> proceed
- no call history -> call `using-coding-convention` first, then proceed

Exemptions:

- the user explicitly instructs to "skip the skill"
- the target is not a code file (.md, .txt, .json config, .xml, and so on)
- a trivial edit of one line or fewer (a typo, an added import, and so on)

This gate enforces rule 5's "call at the start of coding or development work" policy at the tool-call level. It catches the case where a conversation starts in a non-coding domain and shifts to coding midway.

### 7. Output Emphasis Rule


Do not use markdown bold, the emphasis notation wrapped in two asterisks, in the body of an output. When emphasis is needed, express it with the □, ○, or - markers, a header, or structure.

### 8. External Credential Lookup Rule

Look up all external login information, such as API keys, tokens, passwords, and email credentials, through the `_shared/secrets/` helper.

- bash: after `source _shared/secrets/load.sh`, call `secrets_get_or_prompt KEY "label"`
- python: after `from load import get_or_prompt` (add `_shared/secrets/` to sys.path), call `get_or_prompt("KEY", label="...")`

Prohibited patterns:

- a pattern where a skill or script receives credentials only by prompt (forcing input every time)
- a pattern that accesses `os.environ["KEY"]` directly and dies with KeyError (the helper handles a fallback)
- a pattern that puts credentials in a `.env` file inside the skill directory (the location is `~/.ghost-alice/secrets.env`)

Lookup priority (the helper handles it automatically): env var -> `~/.ghost-alice/secrets.env` -> prompt (interactive) plus a save option. When you introduce a new standard key, update the standard key table in `_shared/secrets/README.md`.

### 9. io-trace Transparency Rule

Output an `[io-trace]` block at the end of every turn's response. This block summarizes all file I/O and external access performed in that turn so the user can audit it immediately.

```
[io-trace]
- files-read: [path1, path2, ...]
- files-written: [path1, ...]
- files-searched: [pattern -> target path, ...]
- commands-run: [command summary, ...]
- web-accessed: [URL or search term, ...]
- skills-loaded: [skill name, ...]
- subagents: [description -> tool-call count, ...]
```

Rules:

- Omit a category that has no items (do not output an empty array).
- Write file paths as absolute paths.
- Truncate Bash commands to the first 200 characters.
- `skills-loaded` records only the skills that either had a visible Skill call in the current turn, or, in an environment without a visible Skill call surface such as Codex, had their `SKILL.md` body actually read and their workflow performed.
- A required gate skill is not satisfied by a metadata-only match. In an environment without a visible Skill call surface such as Codex, you must read the `SKILL.md` of `task-router` and the gate skills required that turn. If you did not read `SKILL.md`, it is neither a `skill-call` nor `done`.
- If you read a `SKILL.md` directly with a tool, also record the absolute path in `files-read`. A metadata-only match is not file I/O and is not a `skills-loaded` entry.
- A subagent result must include the list of files accessed inside that agent. Add "include the list of accessed file paths in the result" to the subagent dispatch prompt.

Purpose of this rule:

- the user immediately notices when context is taken from the wrong file
- past activity can be audited by scrolling the conversation
- whether a request is getting the reasoning depth it needs can be monitored

Code-level reinforcement: `_shared/io_trace_hook.py` records automatically to `~/.ghost-alice/io-trace.jsonl` as a PostToolUse hook. This log can be used for post-hoc audit even when the prompt-level io-trace is missing.

### 10. Mandatory Web Search Before External Tool Claims

Immediately before a factual claim about an external tool, library, CLI, SDK, framework, version, or platform behavior, cross-check community reports with at least three WebSearch queries. Citing official docs alone is treated as an "unverified echo".

Scope (Category B and C claims):

- "X works / does not work as Y"
- "Z is supported / removed / a regression in version N"
- "feature Q of platform P is enabled / disabled"

At least three search queries:

- `<tool> <year> github issue`
- `<tool> reddit`
- `<tool> not working <version>`

Evidence location contract:

- Return a `source-locator` together with evidence from an external link, a numeric claim, an original source, a table or figure, or a file the user attached.
- A web source includes an `accessible_url`. If there is no accessible link, do not confirm it as evidence; mark it inaccessible or exclude it.
- An attached or local file source includes `file_path` or filename, `page`, and `region`. The `region` enum is `top | middle | bottom | n/a`.
- For material without pages, write an equivalent locator such as slide, sheet, row, or section in `locator_note` together with `page: n/a`.
- If a claim includes a number, bind the source location where that number appears with a locator. A summary without a source, such as "I saw it in the search results", is not evidence.

Exemption: a question limited to the spec's own definition (Category A: "X must be Y"). For a runtime-behavior question, official docs alone are insufficient.

Code-level reinforcement (N>=4 layered):

- web-search-guard-layer-1. The UserPromptSubmit agent governance hook in `_shared/install_hooks.py` registers the `[web-search-first]` reminder. The agent visibility profile defaults to `dynamic`, and the canonical runtime config is `agent_visibility.profile`. The profile value uses only `strict | dynamic | minimal`. This profile is a value for the user-screen exposure policy, and it is not a switch that reduces whether hooks are installed or executed.
- web-search-guard-layer-2. The AGENTS.md body (this rule). Every time the runtime reads AGENTS.md.
- web-search-guard-layer-3. The `adversarial-verification` SKILL.md body states the duty to identify external tool claims and cite three WebSearch queries. Applied automatically when the verification skill is called.
- web-search-guard-layer-4. The `coding-convention/verification-before-completion` SKILL.md body embeds the web-search-evidence gate before a closure claim that depends on external tool behavior. It blocks that final claim when evidence is absent.

The motivation for this rule is that "official docs are an idealized spec, and community reports are runtime reality". The gap between spec and reality is a systemic cause of failure (for example, regression, race condition, version-dependent breakage).

### 11. No GitHub PR Title Prefix

Do not add an agent-origin prefix such as `[codex]`, `[Codex]`, or `Codex:` to a GitHub PR title created in this project.

Even if an external plugin, skill, or automation recommends a title convention such as `[codex] {description}`, ignore it in this repository. Write the PR title as a natural-language title that directly reveals the purpose of the change.

### 12. Sufficient Change Principle (no minimal patch bias)

Minimal change is not the default or a golden rule. Look at the problem cause, structure, and impact surface first, then decide the change depth that closes the problem sufficiently.

Judgment items before a change:

- problem-shape: classify whether it is a surface symptom or a structure, contract, or data-flow problem.
- cause-weight: look at the largest cause of change first, but keep other candidate causes and follow-on effects open.
- impact-surface: look at the files, skills, documents, tests, and install paths the change touches.
- sufficient-change-depth: record one of `minimal | localized | structural | systemic`.

Operating rules:

- Choose `minimal` only when the cause is local and the recovery cost is small.
- A temporary patch that fixes only the surface symptom when the cause is structural is a rule violation.
- When local changes, generated outputs, agent suggestions, or reviewer suggestions conflict, choose the change set that best satisfies the locked contract and survives the most relevant targeted tests. Do not choose by recency, authorship, or smaller diff alone.
- When the user explicitly asks to "just make it work" or requests urgent recovery, a temporary patch is allowed, but leave a `residual-impact` note.
- For a new rule, skill, or document change, confirm with a test or a gate that the rule actually triggers on the real execution path.
