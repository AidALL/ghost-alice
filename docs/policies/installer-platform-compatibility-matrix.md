# Installer Platform Compatibility Matrix

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/installer-platform-compatibility-matrix.md)

This document is the Phase 7 compatibility test contract for the Ghost-ALICE installer.
## Contents

- [Runtime Contract](#runtime-contract)
- [Agent Hook Runtime Contract](#agent-hook-runtime-contract)
- [Hook Message Semantics](#hook-message-semantics)
  - [task-router](#task-router)
  - [session-intent-analyzer](#session-intent-analyzer)
  - [pending merge precheck](#pending-merge-precheck)
  - [tool-checkpoint recovery surface](#tool-checkpoint-recovery-surface)
  - [completion-reminder](#completion-reminder)
- [Shell Matrix](#shell-matrix)
- [CI Commands](#ci-commands)


## Runtime Contract

| Area | Contract | Test owner |
| --- | --- | --- |
| Python runtime | Python 3.11+ accepted, no upper bound, no future-version allowlist | `scripts/run_installer_compat_tests.py`, `scripts.tests.test_install_runtime_detection` |
| Python bootstrap | Default install path attempts package-manager Python setup before aborting; read-only status/doctor/list do not mutate the system | `scripts.tests.test_install_runtime_detection` |
| Python lookup | `python`, `python3`, `python3.*`, broken store stubs, PATH with spaces | `scripts.tests.test_install_runtime_detection` |
| Encoding | UTF-8 mode forced for shell, PowerShell, CMD wrapper | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_install_cmd_wrapper` |
| User home paths | spaces and non-ASCII HOME fixtures stay supported | `scripts.tests.test_installer_asset_inventory`, installer integration tests |
| PSScriptAnalyzer optional | PSScriptAnalyzer optional; parser/static tests are the minimum gate when the module is absent | local full validation |
| Public surface parity | README, docs/index.html, Claude command wrappers stay aligned with `skill-catalog/skills.json` count, list, and targets | `scripts/validate_public_surfaces.py`, `scripts/run_installer_compat_tests.py --group public-surface-contract` |
| Ghost-ALICE fresh clone install policy | Public installs use a fresh `AidALL/ghost-alice` clone plus install. The installer does not rewrite existing remotes, rename local checkout directories, or expose repository migration flags. Installed Claude permission cleanup is limited to managed stale checkout path allow rules. | `scripts.tests.test_source_health_gate`, `_shared.test_install_hooks.TestInstallHook` |
| Agent visibility profile | Default `agent_visibility.profile` is `dynamic`. Allowed values are `strict`, `dynamic`, and `minimal`. The installer accepts `--visibility` and PowerShell `-Visibility` as the primary runtime preference toggles, with `--agent-visibility` and `-AgentVisibility` retained as compatibility aliases. The profile controls the user-facing governance message surface only. It does not suppress hook installation or hook execution, does not change generated hook command contracts, and strict-grade session logging remains always-on. | `_shared.test_runtime_config`, `_shared.test_hook_profile_gate`, `_shared.test_install_hooks`, `scripts.tests.test_install_status_contract` |
| Agent visibility runtime command surface | Claude Code uses `/visibility strict|dynamic|minimal`, Codex uses the trusted `UserPromptSubmit` hook pseudo-command path for `/visibility`, and all platforms can use `_shared/agent_visibility_cli.py`. Documentation must not collapse these into one slash-command spelling. | `scripts.tests.test_install_status_contract` |

## Agent Hook Runtime Contract

The Ghost-ALICE installer does not treat hook files as sufficient merely because they exist. Hook payload and platform permission policy must also be synchronized with the current repo contract. Even if an older hook entry exists, the installer must update managed entries when the runtime payload changes. Hook presence and `agent_visibility.profile` are separate concerns. The profile controls the user-facing message surface; it must not relax missing or drift judgments.

The base session-gate contract describes core behavior. Official privileged adapter addons may extend behavior after their declared hook events, but addon-specific queues, task schemas, and continuation policies stay in the addon and require addon-owned smoke evidence.

Runtime live smoke follows `docs/policies/live-smoke-regression.md`. That procedure sends the same README first 10 lines request to Claude Code, Codex, and Antigravity to observe `task-router`, `verification-before-completion`, concise tool-checkpoint failure surface, and skill activation permission behavior.

| Platform | Runtime surface | Contract | Regression owner |
| --- | --- | --- | --- |
| Claude Code | `~/.claude/settings.json` hooks and `permissions.allow` | Allow `Skill(<name>)` permission for the full set of installed Ghost-ALICE skills. A shortened allowlist that only includes core gates such as `task-router`, `merge-companion`, and `verification-before-completion` is insufficient. | `_shared.test_install_hooks.TestInstallHook` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, bootstrap `AGENTS.md` | Because there is no visible Skill tool, required gates are recorded through `SKILL.md` read plus workflow execution. Hook event names in `hooks.json` are install-surface configuration, not runtime firing proof; gate-completion claims require observed hook payload evidence, runtime smoke, or the hookless/manual fallback wording. `web-search-first` and `tool-checkpoint` payloads are installation and drift verification targets. The agent visibility profile must not reduce hook execution. Windows native Codex gets separate smoke for actual hook payload firing and `SKILL.md` read records. | `_shared.test_install_hooks`, `scripts.check_skill_gate_contract`, Windows native live smoke |

## Hook Message Semantics

### task-router

Every user input is a `task-router` target. Simple questions, opinions, status comments, follow-up questions, and smoke tests all route before the first tool call. "It was already routed in the previous turn" is not a valid skip reason.

### session-intent-analyzer

Every user input is observed by the session intent ledger. Hooks store digest and event data, not raw prompts. When goals, constraints, decisions, or non-goals change, the agent records a compressed delta in `intent-state.json`. `intent-state.json` is update-plus-accumulate state: scalar intent fields are replaced by newer deltas, while list-like constraints, non-goals, questions, criteria, and decisions are deduped or merged by stable id. This context is consumed by `skill-evolution` and `jailbreak-detector`. Deterministic hard-block rules are narrow regression guards for explicit attack signals; gradual multi-turn jailbreak resistance depends on session-intent summary quality and cumulative constraint comparison.

### pending merge precheck

If the SessionStart or UserPromptSubmit hook provides a contract that current platform pending-merge precheck ran and no pending warning exists, runtime records `merge-companion-precheck: clean (hook-verified)` and does not repeat shell manifest checks. Read the current platform manifest directly only when the hook reports an undecided entry or hook evidence is absent. When an undecided entry exists, surface `merge-companion` first; a user-explicit defer/skip may continue with the manifest entry still `decided=false`.

### tool-checkpoint recovery surface

Runtime tool-checkpoint payload does not require routine recovery cost or recovery note fields. The default visible surface keeps `intent` and `why`; `failure-mode-if-wrong` and `recovery-action` appear only when a side effect, forced signal, mismatch, meaningful user decision point, or hard-to-recover action needs them.

### completion-reminder

Before claiming executed work is complete, fixed, successful, or freshly verified, `verification-before-completion` is the lifecycle gate. Routine explanations, meta-discussion, and options do not require it unless they claim finished work or verified results. On Claude Code, where a visible Skill surface exists, use `skill-call: verification-before-completion (this turn)` only after the actual Skill call. On Codex, where no visible Skill surface exists, use the same record only when that `SKILL.md` was actually read and the workflow followed in the current turn.

If the final response `[completion-check]` claims `skill-call: verification-before-completion (this turn)`, the same final response `[io-trace]` `skills-loaded` must include the same skill.

## Shell Matrix

| Environment | Required coverage | Automation |
| --- | --- | --- |
| macOS bash 3.2 | `install.sh` parse and targeted install/status flows | local macOS full validation |
| modern bash | Linux/GitHub Actions `install.sh` smoke and unittest integration | `installer compatibility matrix` workflow step |
| zsh invocation | `zsh install.sh` re-execs under bash before bash-only syntax runs; installer stays bash-owned | `scripts.tests.test_installer_compat_matrix` |
| Linux bash | `install.sh --platform codex --skip-source-health task-router` fixture | `scripts.tests.test_install_preflight_quarantine` |
| WSL | same as Linux bash, with Windows path boundary smoke | manual or Windows CI |
| Git Bash | copy-mode fallback, Codex hook config through `~/.codex/hooks.json`, UTF-8 bridge | runtime detection and Codex hook config tests |
| Windows native Codex hook smoke | `install.ps1 --platform codex` writes Codex bootstrap, `hooks.json`, and `config.toml` with hooks enabled; live smoke observes hook payload firing | `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestCodexHookSupportGuard`, `docs/policies/live-smoke-regression.md` |
| Windows visibility preference smoke | `install.ps1 -Visibility <profile>` and `install.cmd -Visibility <profile>` forward to `_shared/install_hooks.py --visibility`; `-AgentVisibility` remains accepted as a compatibility alias, and status reports hook state and runtime profile separately | `scripts.tests.test_install_status_contract`, Windows PowerShell/CMD live smoke |
| Windows PowerShell 5.1 | UTF-8 BOM retained and parser-compatible; PS5-incompatible syntax is caught by `PSUseCompatibleSyntax` static analysis when the analyzer is available | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_powershell_static_analysis` |
| Windows PowerShell 7.4 LTS cleanup | `install.ps1` removes detected PowerShell 7.4.x MSI products by product-code scoped `msiexec.exe /x ... /quiet /norestart` before resolving the latest 7.6.x MSI; non-MSI entries without a product code are skipped, and 7.5/7.6+, Windows PowerShell 5.1, and unrelated products are not removed | `scripts.tests.test_install_ps1_pwsh_lts`, `scripts.tests.test_powershell_static_analysis`, Windows native live smoke |
| PowerShell 7 | parser-compatible and install flow accepts Python 3.11+; help output exposes the official addon alias and describes `-AddonTag` as branch/tag selection for git URL addon sources | local/CI PowerShell smoke, `scripts.tests.test_install_cmd_wrapper` |
| CMD wrapper | delegates to `install.ps1`, forwards arguments, forces UTF-8, and preserves the official `--addon autopilot` alias through PowerShell binding and addon source preparation; wrapper-facing help exposes the same alias and AddonTag wording | `scripts.tests.test_install_cmd_wrapper` |

## CI Commands

Minimum cross-platform Python gate:

```bash
python3 scripts/run_installer_compat_tests.py
```

Focused groups:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group installer-runtime-detection
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
python3 scripts/run_installer_compat_tests.py --group installer-status-contract
python3 scripts/run_installer_compat_tests.py --group installer-powershell-static
```

Optional PowerShell analyzer gate:

```powershell
Invoke-ScriptAnalyzer -Path ./install.ps1 -Settings ./PSScriptAnalyzerSettings.psd1 -Severity Warning,Error
```
