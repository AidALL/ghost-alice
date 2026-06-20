# Ghost-ALICE OS

Language: 🇺🇸 English | [🇰🇷 한국어](./README_ko.md)

![Ghost-ALICE OS logo](imgs/Ghost-ALICE_logo.png)

> Raise the floor. Make the ghost governable.

Ghost-ALICE OS is an agent governance operating layer that keeps LLM agent work above a dependable quality floor.

It assumes that every agent can have a novice day: it may skip obvious checks, over-trust a plausible answer, forget a constraint, cite a source it did not verify, or expand the task just because momentum feels productive.

Ghost-ALICE OS externalizes the habits of a skeptical operator. It splits work into semantic atoms, checks each atom against evidence and constraints, records source locators for claims, moves focus back and forth across micro/meso/macro/meta layers when mismatch appears, and blocks completion until fresh verification exists.

Agents are not root as a consequence of that philosophy. They request capabilities through governed surfaces instead of owning execution directly.

Ghost-ALICE OS is not a prompt library, a chatbot wrapper, or a loose bundle of skills. It is a governed execution layer for agents that need continuity, boundaries, verification, lifecycle control, and auditability.
## Contents

- [Core Philosophy](#core-philosophy)
- [What Ghost-ALICE OS Governs](#what-ghost-alice-os-governs)
- [Runtime Consequence](#runtime-consequence)
- [Name](#name)
- [Relationship to Agent Skills](#relationship-to-agent-skills)
- [Installation And Update Guide](#installation-and-update-guide)
- [Planning Hub](#planning-hub)
- [Operating Philosophy](#operating-philosophy)
- [Repository Structure](#repository-structure)
- [Skill Authoring And Modification Rules](#skill-authoring-and-modification-rules)
- [Documentation Hub](#documentation-hub)
- [Contributing and Community](#contributing-and-community)
- [License](#license)


## Core Philosophy

Ghost-ALICE OS is a floor system, not a ceiling system. Its primary job is not to raise the maximum output of an expert agent on a good day. Its job is to keep a novice, overloaded, or overly confident agent from falling below an acceptable work-quality floor.

The operating model follows four habits.

- Semantic atomic decomposition: work is split by verification burden, not by visible file count or tool count.
- Mutual verification loop: every intermediate state is compared against schema, SSOT, evidence, user constraints, and stop conditions.
- Source-grounded claims: when the agent cannot decide alone, it must use real external or local sources and leave accessible links or locators.
- Dynamic focus control: the scope of attention is not a one-way expansion. A macro mismatch can send the agent back to a meso sub-task, a micro tool call, or the original structure. User interaction, mismatch location, and verification burden decide the current layer.

The phrase "Agents are not root" is therefore a runtime consequence, not the starting point. Agent creativity is allowed, but execution authority, completion claims, new work creation, and scope expansion pass through governance first.

## What Ghost-ALICE OS Governs

| Surface | Governance role |
| --- | --- |
| User intent | Maintains goals, constraints, locked decisions, non-goals, and stop conditions across context shifts. |
| Skill activation | Routes work to appropriate capabilities instead of letting the agent improvise every procedure. |
| Boundaries | Declares allowed and prohibited surfaces before high-risk or ambiguous work begins. |
| Tool use | Treats files, shell commands, browsers, MCP tools, and external services as governed execution surfaces. |
| Drift resistance | Compares current requests against session intent to detect jailbreaks, instruction overrides, and scope drift. |
| Verification | Forces claims, outputs, and completion statements through closed-loop checks when evidence burden is high. |
| Lifecycle | Tracks installation, updates, local modifications, pending merges, handoffs, and uninstall cleanup. |
| Auditability | Records enough trace to explain what was touched, why it was touched, and which rule allowed it. |

## Runtime Consequence

Agents are not root.

An agent is a user-space reasoning process. It should not directly own execution because unverified execution is how quality drops below the floor. It should request capabilities through a governed runtime layer.

```text
User request
  ↓
session-intent-analyzer
  ↓
governance consumers
  ├─ capability routing
  ├─ boundary / security gates
  └─ completion criteria
  ↓
boundary-contract, when needed
  ↓
skill activation / capability routing
  ↓
execution surface
  ├─ files / shell / browser / MCP / external tools
  └─ documents / code / datasets
  ↓
closed-loop verification
  ↓
audit trace / handoff / lifecycle gate
```

The goal is to turn autonomous intent into controlled, inspectable, and replayable execution without letting useful creativity bypass verification. Internal routing still exists, but it consumes the shared intent state rather than standing at the model's visible front.

## Name

The name Ghost-ALICE holds two opposing forces in tension.

The ghost stands for AI as an opaque black box: useful, fast, and powerful, yet hard to see into and able to act outside direct human attention.

Alice stands for the adaptive governance layer: the part of the system that preserves intent, maintains boundaries, checks evidence, and turns autonomous behavior into inspectable execution.

Ghost-ALICE OS is not a general-purpose operating system or standalone agent runtime. `OS` names the layer that makes governance durable: session gates, installer state, platform adapters, audit traces, and verification contracts.

## Relationship to Agent Skills

Ghost-ALICE OS uses the [Agent Skills Open Standard](https://github.com/agentskills/agentskills) as its capability packaging layer.

In Ghost-ALICE OS, a skill is not just a prompt fragment. It is a governed capability: a versioned folder with instructions, references, scripts, templates, activation rules, compliance checks, and runtime boundaries.

Each skill is managed against Ghost-ALICE Phase 1-5 compliance checks. The upstream Agent Skills format provides the packaging substrate; Ghost-ALICE OS adds session-level governance, routing, verification, lifecycle control, and audit traces.

Ghost-ALICE OS is open to third-party capabilities. Official addons can use a short installer alias, for example `bash install.sh --addon autopilot`, and the installer applies it to detected Claude Code/Codex targets unless `--platform` narrows the target. Anyone can package their own skills as an addon and install them with `bash install.sh --addon-source <path-or-url>`, so the platform grows with community and tenant skills instead of a fixed allowlist. An addon ships an `addons-manifest.json` index plus a per-addon `addon.json` that lists its skills, and an addon skill name must not collide with a core skill. The addon path resolves and lists addon entries, then installs addon skill directories for the selected platform; third-party addon quality still depends on the addon manifest, skill contents, and the same governance checks that apply to core skills. The [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring) documents the manifest format and a worked example.

## Installation And Update Guide

Start with the [team onboarding wiki](https://github.com/AidALL/ghost-alice/wiki/team-onboarding). It covers prerequisites, platform-specific install commands, skill updates, and FAQ.

If `git pull`, merge conflicts, or PowerShell reinstall steps fail during an update, start with the [install troubleshooting wiki](https://github.com/AidALL/ghost-alice/wiki/install-troubleshooting), which can be read without pulling the repo first. The in-repo copy is [docs/getting-started/troubleshooting.md](./docs/getting-started/troubleshooting.md).

Quick install:

```bash
git clone https://github.com/AidALL/ghost-alice.git ~/ghost-alice
cd ~/ghost-alice
bash install.sh
```

Windows PowerShell / CMD:

```powershell
.\install.cmd
```

For `cmd.exe`, omit the leading `.\`:

```bat
install.cmd
```

`install.cmd` is a thin wrapper around `install.ps1` for Windows shells where
PowerShell execution policy or profile loading blocks direct `.ps1` execution.
The Windows install path keeps the Python 3.11+ runtime contract and UTF-8 console setup in `install.ps1`; the wrapper calls it with `-NoProfile -ExecutionPolicy Bypass` and forwards arguments. It does not change the user or machine execution policy.

Agent visibility command surface:

- Claude Code uses `/visibility strict|dynamic|minimal` as its workspace command.
- Codex handles `/visibility` through the trusted `UserPromptSubmit` hook pseudo-command path.
- Every platform can inspect and change the same profile value through `_shared/agent_visibility_cli.py`.
- The install-time default is `dynamic`. Use `bash install.sh --visibility dynamic` or `.\install.cmd -Visibility dynamic` to set the initial profile; `--agent-visibility` and `-AgentVisibility` remain compatibility aliases.
- Visibility changes the user-facing governance message surface only. It does not weaken hook execution, strict-grade logs, or Work-Impact Projection.

Detailed docs:

- [Installation and update guide](./docs/getting-started/installation.md)
- [Troubleshooting](./docs/getting-started/troubleshooting.md)
- [Uninstall cleanup procedure](./docs/getting-started/uninstall.md)

Release validation:

```bash
python3 scripts/validate_public_surfaces.py
```

The validator checks that public docs, command wrappers, and catalog references stay aligned with `skill-catalog/skills.json`. Official addons use short aliases such as `--addon autopilot`; domain and tenant capabilities use `--addon-source`.

The current `skill-catalog/skills.json` snapshot contains top-level 10 skills and 14 coding-convention sub-skills, total 24.

- top-level skills 10 (adversarial-verification, agent-security-scan, boundary-contract, compact-handoff, jailbreak-detector, merge-companion, necessity-gate, session-intent-analyzer, skill-evolution, task-router)
- coding-convention family 14 sub-skills (brainstorming, dispatching-parallel-agents, executing-plans, finishing-a-development-branch, receiving-code-review, requesting-code-review, subagent-driven-development, systematic-debugging, test-driven-development, using-coding-convention, using-git-worktrees, verification-before-completion, writing-plans, writing-skills)

Quick full uninstall:

```bash
bash install.sh --uninstall
```

```powershell
.\install.cmd -Uninstall
```

```cmd
install.cmd -Uninstall
```

## Planning Hub

Public planning guidance lives in [docs/plans/README.md](./docs/plans/README.md). Keep local backlog, private integration notes, and speculative roadmap items out of the public repository.

## Operating Philosophy

- Ghost-ALICE OS is a floor system. It does not try to raise the ceiling for the strongest operator; it protects the minimum acceptable quality of agent work.
- Complex work is not concluded in one pass. It is split into semantic atoms, then each state is checked again against evidence.
- Current state is compared against schema, SSOT, evidence, and constraints. When mismatch appears, the agent repairs it or hands it back to a human.
- Claims that depend on external or local sources must leave accessible links, file locations, or source locators.
- Focus scope is neither fixed nor a one-way expansion. User interaction, mismatch location, and verification burden move work across micro, meso, macro, and meta layers.
- Hook values matter when they change the next work decision: focus, boundary, verification burden, or recovery path. Visibility is secondary to that work-impact judgment.
- Complexity is judged by verification burden more than tool count. Source selection, mapping interpretation, format constraints, and recovery cost can all require another verification loop.
- `calls` expresses only static and sparse relationships. Repeated re-verification loops belong to the runtime procedure.
- Longer design discussion belongs in public wiki or issues, not in bundled release docs.

## Repository Structure

The detailed repository map lives in [docs/reference/repository-structure.md](./docs/reference/repository-structure.md).

## Skill Authoring And Modification Rules

`AGENTS.md` is the SSOT. For detailed wording and platform-specific exceptions, prefer [AGENTS.md](./AGENTS.md) and [platforms/codex/AGENTS.md](./platforms/codex/AGENTS.md).

After creating a new skill or modifying an existing one, pass Phases 1-5 of [official-docs/derived/skill-compliance-checklist.md](./official-docs/derived/skill-compliance-checklist.md).

The session gate contract is generated from [skill-catalog/session-gates.json](./skill-catalog/session-gates.json) and checked with `python scripts/check_skill_gate_contract.py`.

## Documentation Hub

English is the default repository entry path. Korean documents are maintained as paired reader-facing mirrors under `README_ko.md`, `docs/ko/`, and paired Wiki pages. Each English document links to its Korean counterpart, and each Korean document links back to the matching English page.

| Topic | English | Korean |
| --- | --- | --- |
| Root README | README.md | [README_ko.md](./README_ko.md) |
| Document index | [docs/README.md](./docs/README.md) | [docs/ko/README.md](./docs/ko/README.md) |
| Language policy | [docs/concepts/language-policy.md](./docs/concepts/language-policy.md) | [docs/ko/concepts/language-policy.md](./docs/ko/concepts/language-policy.md) |
| Installation and update | [docs/getting-started/installation.md](./docs/getting-started/installation.md) | [docs/ko/getting-started/installation.md](./docs/ko/getting-started/installation.md) |
| Troubleshooting | [docs/getting-started/troubleshooting.md](./docs/getting-started/troubleshooting.md) | [docs/ko/getting-started/troubleshooting.md](./docs/ko/getting-started/troubleshooting.md) |
| Uninstall cleanup | [docs/getting-started/uninstall.md](./docs/getting-started/uninstall.md) | [docs/ko/getting-started/uninstall.md](./docs/ko/getting-started/uninstall.md) |
| Repository structure | [docs/reference/repository-structure.md](./docs/reference/repository-structure.md) | [docs/ko/reference/repository-structure.md](./docs/ko/reference/repository-structure.md) |
| Installer architecture | [docs/reference/installer-architecture.md](./docs/reference/installer-architecture.md) | [docs/ko/reference/installer-architecture.md](./docs/ko/reference/installer-architecture.md) |
| Skill catalog guide | [docs/reference/skills.md](./docs/reference/skills.md) | [docs/ko/reference/skills.md](./docs/ko/reference/skills.md) |
| Runtime gate matrix | [docs/policies/session-gate-matrix.md](./docs/policies/session-gate-matrix.md) | [docs/ko/policies/session-gate-matrix.md](./docs/ko/policies/session-gate-matrix.md) |
| Installer platform compatibility | [docs/policies/installer-platform-compatibility-matrix.md](./docs/policies/installer-platform-compatibility-matrix.md) | [docs/ko/policies/installer-platform-compatibility-matrix.md](./docs/ko/policies/installer-platform-compatibility-matrix.md) |
| Tool output semantics | [docs/policies/tool-output-semantics.md](./docs/policies/tool-output-semantics.md) | [docs/ko/policies/tool-output-semantics.md](./docs/ko/policies/tool-output-semantics.md) |
| Platform adapter compliance | [docs/policies/platform-adapter-compliance.md](./docs/policies/platform-adapter-compliance.md) | [docs/ko/policies/platform-adapter-compliance.md](./docs/ko/policies/platform-adapter-compliance.md) |
| Live smoke regression | [docs/policies/live-smoke-regression.md](./docs/policies/live-smoke-regression.md) | [docs/ko/policies/live-smoke-regression.md](./docs/ko/policies/live-smoke-regression.md) |
| Evaluator artifact contract | [docs/policies/evaluator-artifact-contract.md](./docs/policies/evaluator-artifact-contract.md) | [docs/ko/policies/evaluator-artifact-contract.md](./docs/ko/policies/evaluator-artifact-contract.md) |
| Planning docs | [docs/plans/README.md](./docs/plans/README.md) | [docs/ko/plans/README.md](./docs/ko/plans/README.md) |
| Public release checklist | [docs/release/public-release-checklist.md](./docs/release/public-release-checklist.md) | [docs/ko/release/public-release-checklist.md](./docs/ko/release/public-release-checklist.md) |
| Addon authoring | [wiki: addon authoring](https://github.com/AidALL/ghost-alice/wiki/addon-authoring) | [wiki: addon authoring ko](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko) |

## Contributing and Community

Contributions are reviewed for behavior, safety, documentation, and verification evidence.
We especially want workflow-level feedback about where Ghost-ALICE OS prevents real agent failures, where governance output should be surfaced differently, and where `strict`, `dynamic`, or `minimal` visibility profiles can improve without weakening core gate execution.

- Maintainer: [@garlicvread](https://github.com/garlicvread)
- Public questions, bugs, and feature requests: open a GitHub Issue.
- Private project or security contact: `aidall_manager@aidall.tech`
- Personal email addresses are not monitored for this project.

- Contribution guide: [CONTRIBUTING.md](./CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
- Security policy and private vulnerability reporting: [SECURITY.md](./SECURITY.md)
- Support and where to ask questions: [SUPPORT.md](./SUPPORT.md)
- Release notes: [CHANGELOG.md](./CHANGELOG.md)

Do not include secrets, private prompts, customer data, or local runtime state in public issues or pull requests.

## License

Ghost-ALICE OS project-owned source code and documentation are licensed under
the Apache License, Version 2.0.

See [LICENSE](./LICENSE), [NOTICE](./NOTICE), and
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) for bundled third-party
reference material and provenance notes.
