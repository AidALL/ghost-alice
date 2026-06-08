# Ghost-ALICE Codex Bootstrap

When `install.sh --platform codex` runs, this file is copied to `~/.codex/AGENTS.md` and becomes the Codex global instruction set. install.sh uses the first-line marker `# Ghost-ALICE Codex Bootstrap` to decide overwrite safety, so do not modify this marker line.

The SSOT is the repository root `AGENTS.md`. This file is a summary synchronized for the Codex CLI environment. The rule numbers match the repository root `AGENTS.md` one to one. User instructions and a project-local `AGENTS.md` take precedence over this file.
## Contents

- [Codex First-Turn Contract](#codex-first-turn-contract)
- [Install Locations](#install-locations)
- [Codex Hookless Fallback](#codex-hookless-fallback)
- [Session Gate Contract](#session-gate-contract)
- [Mandatory Rules](#mandatory-rules)
  - [0. Task Routing Gate (required on user input)](#0-task-routing-gate-required-on-user-input)
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
- [Codex CLI Environment Notes](#codex-cli-environment-notes)
  - [Tool Name Mapping](#tool-name-mapping)
  - [Configuration Needed for Subagents](#configuration-needed-for-subagents)
  - [Skill Path References](#skill-path-references)
- [Pending Skill Merge Self-Check (pending-merge bootstrap self-check layer, surface-first rule)](#pending-skill-merge-self-check-pending-merge-bootstrap-self-check-layer-surface-first-rule)
  - [10. Mandatory Web Search Before External Tool Claims](#10-mandatory-web-search-before-external-tool-claims)
  - [11. No GitHub PR Title Prefix](#11-no-github-pr-title-prefix)
  - [12. Sufficient Change Principle (no minimal patch bias)](#12-sufficient-change-principle-no-minimal-patch-bias)


## Codex First-Turn Contract

When a user turn begins in a Codex session, apply the execution contract below before any long explanation.

1. Resolve the pending-merge precheck first.
2. Connect the `session-intent-analyzer` intake.
3. Fix the `jailbreak-detector/downstream-gates` state.
4. Run `task-router`.
5. Leave `[gate-state]` in the first commentary.
6. Surface a `[tool-checkpoint]` once for the user-input tool batch. The hook still checks every tool call; repeated calls in the same session input lineage do not repeat the user-facing checkpoint text unless state changes.

`tool-checkpoint` is not user-input intake. It is a tool-stage `PreToolUse` retry checkpoint.

- hook-stage: PreToolUse
- meaning: tool-call retry checkpoint, not user-input intake

## Install Locations

- Ghost-ALICE user skills: `~/.agents/skills/`
- Codex global instructions: `~/.codex/AGENTS.md` (the destination where this file is installed)

Runtime notes:

- Every Codex install path keeps `~/.agents/skills/` as a copy install. To protect locally modified skills and rules with snapshot and diff, you must run the installer again after an update.
- The focus scope is not fixed and does not expand in only one direction. It moves back and forth across micro, meso, macro, and meta according to the mismatch location and the verification burden. When a large premise or logic is wrong, fix the higher scope. When a small work output is wrong, fix that lower unit.
- After session-intent and downstream gate context are available, task-router emits a reusable routing-surface for work complexity, focus, verification burden, boundary need, and forced visibility. Session intent records semantic facts and decisions; governance surface consumers read the routing-surface instead of creating a competing complexity scale.
- The Ghost-ALICE installer aligns `~/.codex/hooks.json` together with `[features] hooks = true` in `~/.codex/config.toml` to re-enable a locally disabled setting.
- The agent visibility profile defaults to `dynamic`. The canonical runtime config is `agent_visibility.profile`, and the allowed values are `strict | dynamic | minimal`. You can set the initial runtime preference with `--visibility` at install time; `--agent-visibility` remains accepted as a compatibility alias. In a Codex session where the hook is trusted after install you can inspect and change the runtime preference with `/visibility`, `/visibility strict`, `/visibility dynamic`, and `/visibility minimal`. This profile selects the governance message surface shown on the user's screen, and it does not reduce whether hooks such as `web-search-first` and `tool-checkpoint` are installed or executed. The strict-grade session log is preserved regardless of the profile. Codex slash commands are officially documented (https://developers.openai.com/codex/cli/slash-commands), but user-defined slash command behavior is version-dependent and Codex does not auto-invoke slash commands from instructions the way Claude does, so for a stable profile change prefer `python3 _shared/agent_visibility_cli.py set <profile>`.
- Work-Impact Projection classifies hook-internal values by whether they change the work boundary, focus layer, verification burden, or recovery path. Hook execution and the strict audit log are never reduced.
- `agent_visibility.profile` selects the governance message surface shown on the user's screen; it does not gate hook execution, strict logging, or work-impact classification. Forced/risk/gate values and failed verification always break through as user surface forced and model hint full. Routine/debug values stay full in the strict log, but they are omitted from model hints unless they change focus, boundary, verification, or recovery. Unknown, ambiguous, or failed values fail closed to fuller surface and reopen focus through the routing/scope-reopen path.
- Goal: only values that can change the next work decision affect focus, boundary, verification, or recovery. Token reduction is a consequence, not a metric.
- In a session that has hook payload evidence and has finished hook review and trust, treat tool-checkpoint as a hook-enforced retry point. In that case the agent leaves a `[tool-checkpoint]` after a hook denial and retries the same tool call. In Codex this surface is a tool-call retry checkpoint with `hook-stage: PreToolUse`, and it is not user-input intake. Hook timing enforcement does not weaken the semantic gate. Every checkpoint carries at least `intent` and `why`. Add `procedure` when it changes the next work decision or clarifies a non-routine step. Add `contract-ref` and `contract-check` when a boundary-contract is active. Add `localized-human-note`, `rejected-alternatives`, `unverified-premises`, and `failure-mode-if-wrong` only when a side effect, forced signal, mismatch, or meaningful user decision point makes those fields useful. Add `recovery-action` only when a mismatch, scope reopen, external side effect, or hard-to-recover action needs a concrete next step.
- In a session that has no hook payload evidence or is before hook review and trust, apply the bootstrap and skill placement plus the hookless/manual fallback.

## Codex Hookless Fallback

If hooks are disabled in the Codex session, if the session is before hook review and trust, or if no hook payload evidence is observed in the current turn, do not claim the task-router, session-intent, completion, or io-trace reminders as hook evidence. In that case the bootstrap explicitly follows the full fallback procedure below.

- Apply the `Codex First-Turn Contract` at the top first. Even in the hookless/manual fallback, `session-intent-analyzer` branches to the `skill-evolution` report-only branch and to `jailbreak-detector`, and the order in which `task-router` runs only after the `jailbreak-detector` downstream gate is not reversed.
- Do not store the raw user input, and leave a digest-only intake status in the session-intent-analyzer ledger. The agent augments a semantic delta only when the user's intent, constraints, decisions, or completion criteria materially change.
- The task-router reminder hook releases task-router when a session-intent preflight exists and there is no current-lineage block gate. The absence of `downstream-gates.json` is treated as a silent allow meaning there is no current-lineage block. After release, task-router reads the session intent ledger and performs skill assignment after atomic meaning decomposition.
- When task-router outputs `boundary-contract: required`, apply `boundary-contract` before any other tool call.
- Runtime hooks surface one full `[tool-checkpoint]` per session input lineage and keep checking later tool calls silently in that same lineage.
- A routine inspection batch can be referenced briefly as `[tool-checkpoint:batch]`, and checking the output of an already started process can be referenced briefly as `[tool-checkpoint:continuation]`.
- `[tool-checkpoint:continuation]` refers only to output polling of the same process, session, or tool-call id. A new user input, current-lineage block/deny, mismatch, or other state change returns to a surfaced `[tool-checkpoint]`.
- Simple polling of the same ref is not an obligation to repeat output. It is an allowed abbreviation to avoid repeating the full gate. Surface it only on the first occurrence in the input lineage or when state changes.
- Do not infer whether an action is safe from tool-call identity or payload content. The decision depends only on the current-lineage block gate and the silent allow invariant.
- Gate schemas such as `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, and `[io-trace]` follow the English canonical narrative + English control surface principle.
- When a final response claims executed work is complete, fixed, successful, or freshly verified, leave a `[completion-check]` block that connects acceptance criteria with fresh evidence.
- Leave an `[io-trace]` block at the end of the response.
- If you omit any of these, fill it in immediately and then continue.

## Session Gate Contract

The session gate SSOT is the repository `skill-catalog/session-gates.json` and `docs/policies/session-gate-matrix.md`. After install, the Codex runtime follows the minimum contract below.

- First intake: `session-intent-analyzer`
- After the session-intent-analyzer intake and the jailbreak-detector downstream gate, and before downstream work or a tool call: `task-router`
- Development turn: `using-coding-convention`
- Bug fix turn: `systematic-debugging` -> `test-driven-development`
- Before claiming executed work is complete, fixed, successful, or freshly verified: `verification-before-completion`
- Before commit or push: `finishing-a-development-branch`
- Immediately after task-router routes `boundary-contract: required`: `boundary-contract`

Always leave the block below in the first commentary.

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

Hard sequence: skill load/call -> fresh verification -> [completion-check]. Before claiming executed work is complete, fixed, successful, or freshly verified, load or call `verification-before-completion` for the current turn, run and read the fresh verification, and only then write `[completion-check]` with `skill-call: verification-before-completion (this turn)`. If any step is missing or out of order, the completion-check is invalid.

The `skill-call:` line is a factual record that the relevant skill workflow was actually performed in the current turn. Because Codex has no visible Skill tool, considering a skill as a routing candidate from the skill description and metadata exposed to the system is not a `skill-call:`. Record it only when the skill's `SKILL.md` was actually read and the procedure was followed.

Points to follow in an environment without a Codex visible Skill surface:
- Always read the skill's `SKILL.md` before marking a required gate as complete.
- Do not treat a gate as complete based only on metadata, description, memory, a prior turn, or an "already know it" reason.
- If you did not read `SKILL.md` in the current turn, do not list that skill in `skill-call:`. That gate is still pending.
- Apply the same standard to simple tasks, already-routed tasks, and tasks where the metadata looks sufficient.

## Mandatory Rules

### 0. Task Routing Gate (required on user input)

This procedure is a quality-maintenance device that the user confirmed across repeated work. Realigning the goal, constraints, output, and verification criteria on every user input preserves the user intent, the work scope, and the verification quality. If the agent skips it based only on a judgment of "a simple follow-up" or "the procedure is excessive", it can lead to stale routing and insufficient verification.

In every conversation, after the session-intent-analyzer intake and the jailbreak-detector downstream gate, and before downstream work or a tool call, call the `task-router` skill. Check its applicability regardless of domain, including coding, documentation, research, and chores.

In Codex the skill description and metadata are exposed in the system context, but that alone is not treated as satisfying the required gate. After the session-intent-analyzer intake and the jailbreak-detector downstream gate, read `~/.agents/skills/task-router/SKILL.md` and perform the `task-router` workflow to scan the skill descriptions loaded into the system. task-router is a consumer of the session-intent and jailbreak gate context, and it does not own raw user intent, the ledger, the jailbreak decision, the downstream gate state, or tool permission. Record the output, verification, and lifecycle skill matching results, then start the work. If that turn requires additional gates such as `using-coding-convention`, `systematic-debugging`, `test-driven-development`, `verification-before-completion`, or `finishing-a-development-branch`, you must also read that skill's `SKILL.md`. Record every `SKILL.md` read in the `files-read` of `[io-trace]` with an absolute path. A metadata-only match is not a file read and is not a `skills-loaded` entry. Do not skip this gate.

The normal order is `pending-merge precheck -> session-intent-analyzer -> jailbreak-detector/downstream-gates -> task-router -> downstream/tool-checkpoint`. Describing it as `task-router -> session-intent-analyzer`, or arranging the execution order so that it skips passing through the detector, is a rule violation.

### 0-B. Session Intent Ledger Gate

On every user input, `session-intent-analyzer` updates the intake of the per-session intent ledger. On a platform where the hook fires, the UserPromptSubmit hook does not store the raw prompt. It leaves `input_digest`, `input_char_count`, `intake_status=observed`, and `intent_delta_status=not-provided`, and it updates the `.tmp/session-intent/<platform>/current-session.json` pointer at the Ghost-ALICE repository root. The agent augments a compressed delta in the `intent-state.json` of the same session id only when the user's goals, constraints, decisions, non-goals, open questions, or completion criteria materially change. `intent-state.json` is an update-plus-accumulate state. Scalar intent such as `current_goal` and `user_intent_summary` is corrected to the latest delta when there is a semantic delta, and list information such as constraints, non-goals, open questions, criteria, and decisions accumulates by deduplication or merge by id.

The hook's digest-only observation is evidence of intake completion. A semantic delta is required only when the user's intent, constraints, decisions, or completion criteria change. If no delta is needed, you can leave the `last_semantic_delta_status=not-provided` state as is and mark `session-intent-analyzer: done`. If a delta is needed but was not recorded, it is `hook-observed`.

This ledger is the input context for `skill-evolution` and `jailbreak-detector`. Do not store the raw prompt, the full conversation, tool output, system or developer instructions, or raw secrets. Promoting long-term memory without user approval is also prohibited. The deterministic hard-block rule is a narrow regression guard for explicit, high-confidence attack signals, and it is not a proof that every jailbreak is blocked. A gate block derives only from the meaning judgment that the model recorded (`model_security_decision`). Progressive jailbreak resistance across multiple turns depends on the quality of the intent summary, correction, and accumulation in `session-intent-analyzer` and on the quality of the accumulated-constraint comparison in `jailbreak-detector`.

The pending-merge precheck is a pre-routing and session-start layer that completes before the user-input governance graph begins. After this precheck is clean or surfacing ends through an explicit user defer or skip, the runtime hook graph fixes the session intent ledger state first. User input -> the `session-intent-analyzer` hook records the digest, the session ledger, and the `current-session.json` pointer and allows -> `skill-evolution` and `jailbreak-detector` consume the same session temp files -> `skill-evolution` terminates as a report-only branch -> `jailbreak-detector` records `model_security_decision` in the ledger and carries only a current-lineage block to `downstream-gates.json` -> the task-router reminder hook confirms the session-intent preflight and the absence of a current-lineage block and releases with a silent allow -> `task-router` reads the session-intent ledger and performs only atomic meaning decomposition and the routing decision -> the tool-stage `tool-checkpoint` looks at the current-lineage block gate. If `opened=false` or `decision=block`, it denies. An absent gate or any other state is a silent allow. `tool-checkpoint` does not use tool-call identity, payload content, or audit, log, and correlation metadata as decision input. Audit, log, and correlation metadata stay outside the decision body. `tool-checkpoint` is a `PreToolUse` checkpoint and is not user-input intake.

### 1. Mandatory Official Spec Verification After Writing or Modifying a Skill

After writing a new skill or modifying an existing skill, you must pass Phase 1 through Phase 5 of `official-docs/derived/skill-compliance-checklist.md`. If there is a violating item, fix it and re-verify. Do not proceed to testing, evaluation, or deployment until every item passes.

### 2. Language Tone Rule

The response language follows the user's input language. Answer in English for English input and in Japanese for Japanese input. Apply the tone rule below only when writing Korean outputs such as proposals, official letters, government project documents, or Korean reports.

- Korean outputs unify to the plain declarative style.
- Honorific endings and casual banmal endings are both prohibited in Korean outputs.
- English skills, English outputs, and other-language outputs are not subject to this rule and use that language's formal register.

### 3. Progressive Disclosure Principle

- SKILL.md: within 500 lines, core guidance only
- references/: detailed reference documents, loaded when needed
- scripts/: executable code
- a reference file over 300 lines must include a TOC

### 4. Frontmatter Rule

- `name`: lowercase and hyphens, must match the directory name
- `description`: 250 characters or fewer recommended, 1024 characters or fewer required
- `compatibility`: state it when there is an environment dependency

### 5. Mandatory coding-convention Family Call

In every conversation that begins coding or development work, including writing or modifying a skill, call the `coding-convention/using-coding-convention` entry point first. Call it if there is even a one percent chance it applies. If it does not fit the situation after the call, drop it then. Because Codex loads skills natively, follow the instructions of `using-coding-convention`.

Treat recommendations, option proposals, and status judgments as claims subject to verification. Even for something just confirmed in the same session, pass the gate again on a new turn.

### 6. Write/Edit Code File Gate

Immediately before calling Write or Edit on a code file, check whether `using-coding-convention` has been called in the current conversation. If there is no call history, call it first and then proceed. Exemptions: the user explicitly instructs to skip the skill, the target is not a code file, or it is a trivial edit of one line or fewer.

### 7. Output Emphasis Rule

Do not use markdown bold, the emphasis notation wrapped in two asterisks, in the body of an output. When emphasis is needed, express it with the □, ○, or - markers, a header, or structure.

### 8. External Credential Lookup Rule

Look up all external login information, such as API keys, tokens, passwords, and email credentials, through the `_shared/secrets/` helper. The location is `~/.ghost-alice/secrets.env`.

- bash: after `source _shared/secrets/load.sh`, call `secrets_get_or_prompt KEY "label"`
- python: after `from load import get_or_prompt`, call `get_or_prompt("KEY", label="...")`

Prohibited patterns

- A pattern where a skill or script receives credentials only by prompt, forcing input every time.
- A pattern that accesses `os.environ["KEY"]` directly and dies with KeyError. The helper handles a fallback.
- A pattern that puts credentials in a `.env` file inside the skill directory.

The helper handles the lookup priority automatically: env var -> `~/.ghost-alice/secrets.env` -> prompt plus a save option. In a Codex install environment the `_shared/secrets/` path resolves to `~/.agents/skills/_shared/secrets/`.

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
- `skills-loaded` records only the skills whose `SKILL.md` body was actually read and whose workflow was performed in the current turn.
- A required gate skill is not satisfied by a metadata-only match. You must read the `SKILL.md` of `task-router` and of the gate skills required that turn. If you did not read `SKILL.md`, it is neither a `skill-call` nor `done`.
- If you read a `SKILL.md` directly with a tool, also record the absolute path in `files-read`. A metadata-only match is not file I/O and is not a `skills-loaded` entry.
- A subagent result must include the list of files accessed inside that agent.

Code-level reinforcement: `_shared/io_trace_hook.py` records automatically to `~/.ghost-alice/io-trace.jsonl` as a PostToolUse hook.

## Codex CLI Environment Notes

### Tool Name Mapping

Skill bodies are written against the Claude Code tool names (`Read`, `Write`, `Edit`, `Bash`, `Skill`, `Task`, `TodoWrite`). In Codex, map them as follows.

- A `Skill` call is unnecessary. Codex receives skill metadata as native context, but recognizing metadata is not loading or running a skill. Record a required gate skill in `skill-call:` and `skills-loaded` only after actually reading `SKILL.md` and following the procedure.
- `Task` subagent dispatch -> `spawn_agent`. For details see `coding-convention/using-coding-convention/references/codex-tools.md`.
- `TodoWrite` -> `update_plan`
- `Read`, `Write`, `Edit`, and `Bash` use the native tools.

### Configuration Needed for Subagents

You must set `[features] multi_agent = true` in the Codex config (`~/.codex/config.toml`) for `spawn_agent` to be enabled. For details see `codex-tools.md`.

### Skill Path References

The `${CLAUDE_SKILL_DIR}` variable in SKILL.md is a Claude Code only variable. It is not set in Codex, so replace that path with a relative path (`scripts/`, `references/`) or with the actual install path (`~/.agents/skills/<skill>/`).

## Pending Skill Merge Self-Check (pending-merge bootstrap self-check layer, surface-first rule)

Immediately after every session start, and before writing the first commentary, do the following.

1. Check whether the current turn or the session-start hook output contains a codex pending-merge precheck result.
2. If the hook reported an undecided entry, surface merge-companion first and show the status and options to the user.
3. If the hook issued no pending warning and provided a contract that it performed the current platform precheck, record `merge-companion-precheck: clean (hook-verified)` and do not run an additional shell manifest check.
4. If there is no hook evidence or the environment is hookless or manual, inspect `~/.ghost-alice/pending-merges/codex/manifest.json` directly.
5. On direct inspection, if there is an undecided entry, surface merge-companion first. If the user explicitly defers or skips, you can continue with that pending merge left as `decided=false`. If the manifest itself is absent, every entry is `decided=true`, or JSON parsing fails, it passes (omit even a one-line notice).
6. Skip it only when the user explicitly instructs a "merge-companion self-check exemption".

This is a prose layer that operates in parallel with the SessionStart hook (session-start layer) and the UserPromptSubmit hook payload (user-prompt layer). If the hook already performed the current platform precheck, reuse that result, and if the hook failed or the environment is hookless, check directly. Surfacing first means surfacing first; it does not mean forcing a merge or discard decision.

### 10. Mandatory Web Search Before External Tool Claims

Immediately before a factual claim about an external tool, library, CLI, SDK, framework, version, or platform behavior, cross-check community reports with at least three WebSearch queries. Citing official docs alone is treated as an "unverified echo".

Scope (Category B and C claims):
- "X works / does not work as Y"
- "Z is supported / removed / a regression in version N"
- "feature Q of platform P is enabled / disabled"

At least three search queries: `<tool> <year> github issue`, `<tool> reddit`, `<tool> not working <version>`.

Evidence location contract:
- Return a `source-locator` together with evidence from an external link, a numeric claim, an original source, a table or figure, or a file the user attached.
- A web source includes an `accessible_url`. If there is no accessible link, do not confirm it as evidence; mark it inaccessible or exclude it.
- An attached or local file source includes `file_path` or filename, `page`, and `region`. The `region` enum is `top | middle | bottom | n/a`.
- For material without pages, write an equivalent locator such as slide, sheet, row, or section in `locator_note` together with `page: n/a`.
- If a claim includes a number, bind the source location where that number appears with a locator. A summary without a source is not evidence.

Exemption: a question limited to the spec's own definition (Category A). For a runtime-behavior question, official docs alone are insufficient.

Code-level reinforcement follows the SSOT (repository root AGENTS.md rule 10).

### 11. No GitHub PR Title Prefix

Do not add an agent-origin prefix such as `[codex]`, `[Codex]`, or `Codex:` to a GitHub PR title created in this project.

Even if an external plugin, skill, or automation recommends a title convention such as `[codex] {description}`, ignore it in this repository. Write the PR title as a natural-language title that directly reveals the purpose of the change.

### 12. Sufficient Change Principle (no minimal patch bias)

Minimal patch is not the default or a golden rule. Look at the problem cause, structure, and impact surface first, then decide the change depth that closes the problem sufficiently.

Judgment items before a change:
- problem-shape: classify whether it is a surface symptom or a structure, contract, or data-flow problem.
- cause-weight: look at the largest cause of change first, but keep other candidate causes and follow-on effects open.
- impact-surface: look at the files, skills, documents, tests, and install paths the change touches.
- sufficient-change-depth: record one of `minimal | localized | structural | systemic`.

Operating rules:
- Choose `minimal` only when the cause is local and the recovery cost is small.
- A temporary patch that fixes only the surface symptom when the cause is structural is a rule violation.
- When the user explicitly asks to "just make it work" or requests urgent recovery, a temporary patch is allowed, but leave a `residual-impact` note.
- For a new rule, skill, or document change, confirm with a test or a gate that the rule actually triggers on the real execution path.
