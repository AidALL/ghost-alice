# Installer Platform Compatibility Matrix

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/installer-platform-compatibility-matrix.md)

This document is the compatibility test contract for the Ghost-ALICE installer. The [Core architecture](../../architecture.md) describes the responsibilities and return paths.

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
| Encoding | UTF-8 mode forced for shell entrypoints and native wrappers | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_install_cmd_wrapper` |
| User home paths | spaces and non-ASCII HOME fixtures stay supported | `scripts.tests.test_installer_asset_inventory`, installer integration tests |
| PSScriptAnalyzer optional | PSScriptAnalyzer optional; parser/static tests are the minimum gate when the module is absent | local full validation |
| Public surface parity | README, index.html, Claude command wrappers stay aligned with `skill-catalog/skills.json` count, list, and targets | `scripts/validate_public_surfaces.py`, `scripts/run_installer_compat_tests.py --group public-surface-contract` |
| Ghost-ALICE fresh clone install policy | Public installs use a fresh `AidALL/ghost-alice` clone plus install. The installer does not rewrite existing remotes, rename local checkout directories, or expose repository migration flags. Installed Claude permission cleanup is limited to managed stale checkout path allow rules. | `scripts.tests.test_source_health_gate`, `_shared.test_install_hooks.TestInstallHook` |
| Agent visibility profile | The [agent visibility profile](../getting-started/installation.md#agent-visibility-profile) section gives the default, the allowed values, the `--visibility` flag and the `--agent-visibility` alias. Native wrapper compatibility aliases remain implementation-test coverage. The profile does not change generated hook command contracts. | `_shared.test_runtime_config`, `_shared.test_hook_profile_gate`, `_shared.test_install_hooks`, `scripts.tests.test_install_status_contract` |
| Agent visibility runtime command surface | [Slash commands by platform](../getting-started/installation.md#slash-commands-by-platform) gives each platform's command. Documentation must not present `/visibility` as a Codex command. | `scripts.tests.test_install_status_contract` |

## Agent Hook Runtime Contract

The Ghost-ALICE installer does not treat hook files as sufficient merely because they exist. Hook payload and platform permission policy must also be synchronized with the current repo contract. Even if an older hook entry exists, the installer must update managed entries when the runtime payload changes. The agent visibility profile must not relax missing or drift judgments.

[Responsibilities and flow](../../architecture.md#responsibilities-and-flow) assigns the recording and authentication of work and judgments to Core code. An addon registers observational hooks at its declared native events, and the Core runner never lets them block the host. Addon Python code manages its own records; the current-session assistant follows addon instructions to judge additional work and require evidence for the affected addon behavior.

Runtime live smoke follows [live-smoke-regression](live-smoke-regression.md#purpose), which runs one small request on each claimed installed target and lists the signals it observes.

| Platform | Runtime surface | Contract | Regression owner |
| --- | --- | --- | --- |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`, `settings.json` hooks, and `permissions.allow` | Install the self-contained `platforms/claude/CLAUDE.md` managed block so arbitrary-working-directory sessions receive the common contract and the hookless fallback. Preserve markerless user-owned files through a proposal that does not count as installed. Allow `Skill(<name>)` permission for the full set of installed Ghost-ALICE skills; a shortened core-only allowlist is insufficient. | `scripts.tests.test_global_rule_blocks`, `scripts.tests.test_validate_entrypoints`, `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestInstallHook` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, bootstrap `AGENTS.md` | Because there is no visible Skill tool, required gates are recorded through `SKILL.md` read plus workflow execution. Hook event names in `hooks.json` are install-surface configuration, not runtime firing proof; gate-completion claims require observed hook payload evidence, runtime smoke, or the hookless/manual fallback wording. `tool-checkpoint` payloads are installation and drift verification targets; external-tool evidence selection follows the verification reference when a claim needs it. Windows native Codex gets separate smoke for actual hook payload firing and `SKILL.md` read records. | `_shared.test_install_hooks`, `scripts.check_skill_gate_contract`, Windows native live smoke |

The Codex managed loader declares the installed `~/.codex/ghost-alice-governance.md` as the common instruction source. Work reads that source together with project-specific constraints; a direct answer uses the loader's no-tool rule. Status and doctor require the declared companion regardless of prose headings. The installer does not increase unrelated projects' instruction budgets. User-owned governance files remain protected, and a symlink at the Codex common-contract path prevents replacement. See [common governance and project instructions](../getting-started/installation.md#common-governance-and-project-instructions).

## Hook Message Semantics

### task-router

The common contract's [Work Selection](../../platforms/common-governance.md#work-selection) section sets whether an input gets a direct answer or follows task-router as work.

### session-intent-analyzer

The installer registers session-intent-analyzer as the single UserPromptSubmit intake owner and as the SessionStart session-receipt hook. The session gate matrix's [Session Intent Ledger Contract](session-gate-matrix.md#session-intent-ledger-contract) owns how intake records each input.

### pending merge precheck

[merge-companion](../../merge-companion/SKILL.md#entry-and-activation) sets what the SessionStart pending-merge report means and when the current-session assistant checks the manifest and reviews its entries.

### tool-checkpoint recovery surface

Runtime tool-checkpoint payload does not require routine recovery cost or recovery note fields. The common contract's io-trace Transparency Rule defines the visible checkpoint fields.

### completion-reminder

Installed completion hooks consume Core's current-input and exact-answer assessment. [Evidence and native delivery](../../architecture.md#evidence-and-native-delivery) describes the two judgments that Core authenticates. Core's Stop observer consumes that assessment and blocks nothing.

Claude retains its one-retry budget and visible unresolved-defect notice; Codex blocks genuine completion-evidence defects. [Verification-before-completion](../../coding-convention/verification-before-completion/SKILL.md) owns claim scope and control formats. Its [review reference](../../coding-convention/verification-before-completion/references/response-review.md) owns recording recovery and delivery; [tool output semantics](tool-output-semantics.md#review-output-and-continuation) names returned states. Installer tests check those registered payloads and platform mappings, not the truth of model judgments.

## Shell Matrix

| Environment | Required coverage | Automation |
| --- | --- | --- |
| macOS bash 3.2 | `install.sh` parse and targeted install/status flows | local macOS full validation |
| modern bash | Linux/GitHub Actions `install.sh` smoke and unittest integration | `installer compatibility matrix` workflow step |
| zsh invocation | `zsh install.sh` re-execs under bash before bash-only syntax runs; installer stays bash-owned | `scripts.tests.test_installer_compat_matrix` |
| Linux bash | `install.sh --platform codex --skip-source-health task-router` fixture | `scripts.tests.test_install_preflight_quarantine` |
| WSL | same as Linux bash, with Windows path boundary smoke | manual or Windows CI |
| Git Bash | copy-mode fallback, Codex hook config through `~/.codex/hooks.json`, UTF-8 bridge | runtime detection and Codex hook config tests |
| Windows native Codex hook smoke | Native Windows Codex installer path `.\install.cmd --platform codex` writes Codex bootstrap, `hooks.json`, and `config.toml` with hooks enabled; live smoke observes hook payload firing | `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestCodexHookSupportGuard`, `docs/policies/live-smoke-regression.md` |
| Windows visibility preference smoke | Native wrapper visibility forwarding reaches `_shared/install_hooks.py --visibility`; visibility aliases remain accepted as compatibility coverage, and status reports hook state and runtime profile separately | `scripts.tests.test_install_status_contract`, Windows PowerShell/CMD live smoke |
| Windows PowerShell 5.1 | Retired. `install.ps1` keeps a UTF-8 BOM and Windows PowerShell 5.1 syntax (`PSUseCompatibleSyntax` with target 5.1) so that 5.1 reaches its first statement, which prints `PowerShell 7.6 or newer is required` with `winget install --id Microsoft.PowerShell --exact` and exits 1 | `scripts.tests.test_thin_shells`, `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_powershell_static_analysis` |
| PowerShell 7 | The 7.6 LTS line is the baseline: `install.ps1` runs under PowerShell 7.6 or newer and stops older 7.x with the same requirement; the installer installs no PowerShell. It sets UTF-8 console I/O, prepares Python 3.11+, turns the earlier PowerShell parameter names into the cli flags, passes every argument in order to `_shared/installer/cli.py` and returns that exit code | `scripts.tests.test_thin_shells` (runs `install.ps1` where `pwsh` exists, as on the CI runner), `scripts.tests.test_powershell_static_analysis` |
| CMD wrapper | Delegates to `install.ps1` with `pwsh.exe`, or with `powershell.exe` when `pwsh.exe` is absent, where the `install.ps1` version check prints the requirement; forwards arguments, forces UTF-8 and returns the exit code. Options and help come from the Python installer | `scripts.tests.test_install_cmd_wrapper`, `scripts.tests.test_thin_shells` |

## CI Commands

Minimum cross-platform Python gate:

```bash
python3 -m pip install pytest
python3 scripts/run_installer_compat_tests.py
```

The `shared-all` group runs shared unittest discovery and the pytest functions in `_shared/test_install_destination.py`, `_shared/test_retired_detector_stage.py`, and `_shared/test_retired_discovery.py`; either execution failing fails the group.

Focused groups:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group installer-runtime-detection
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
python3 scripts/run_installer_compat_tests.py --group installer-status-contract
python3 scripts/run_installer_compat_tests.py --group installer-powershell-static
```

Optional analyzer coverage is exposed through the `installer-powershell-static` group above; direct analyzer invocation is intentionally not part of public install guidance.
