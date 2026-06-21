# Installer Platform Compatibility Matrix

언어: [🇺🇸 English](../../policies/installer-platform-compatibility-matrix.md) | 🇰🇷 한국어

이 문서는 Ghost-ALICE installer의 Phase 7 compatibility test contract다.
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
| Python runtime | Python 3.11+ accepted, upper bound 없음, future-version allowlist 없음 | `scripts/run_installer_compat_tests.py`, `scripts.tests.test_install_runtime_detection` |
| Python bootstrap | default install path는 abort 전에 package-manager Python setup을 시도한다. read-only status/doctor/list는 system을 mutate하지 않는다 | `scripts.tests.test_install_runtime_detection` |
| Python lookup | `python`, `python3`, `python3.*`, broken store stubs, spaces in PATH | `scripts.tests.test_install_runtime_detection` |
| Encoding | shell, PowerShell, CMD wrapper에서 UTF-8 mode 강제 | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_install_cmd_wrapper` |
| User home paths | spaces와 non-ASCII HOME fixtures 지원 유지 | `scripts.tests.test_installer_asset_inventory`, installer integration tests |
| PSScriptAnalyzer optional | PSScriptAnalyzer는 optional이다. module이 없으면 parser/static tests가 minimum gate다 | local full validation |
| Public surface parity | README, docs/index.html, Claude command wrappers는 `skill-catalog/skills.json` count, list, targets와 정렬되어야 한다 | `scripts/validate_public_surfaces.py`, `scripts/run_installer_compat_tests.py --group public-surface-contract` |
| Ghost-ALICE fresh clone install policy | public installs는 fresh `AidALL/ghost-alice` clone plus install을 사용한다. installer는 existing remotes를 rewrite하거나 local checkout directories를 rename하거나 repository migration flags를 expose하지 않는다. installed Claude permission cleanup은 managed stale checkout path allow rules로 제한된다 | `scripts.tests.test_source_health_gate`, `_shared.test_install_hooks.TestInstallHook` |
| Agent visibility profile | default `agent_visibility.profile`은 `dynamic`다. allowed values는 `strict`, `dynamic`, `minimal`이다. installer는 `--visibility`와 PowerShell `-Visibility`를 primary runtime preference toggle로 받고, `--agent-visibility`와 `-AgentVisibility`는 compatibility alias로 유지한다. profile은 user-facing governance message surface만 제어한다. hook installation/execution을 suppress하지 않고 generated hook command contracts를 바꾸지 않으며 strict-grade session logging은 always-on이다 | `_shared.test_runtime_config`, `_shared.test_hook_profile_gate`, `_shared.test_install_hooks`, `scripts.tests.test_install_status_contract` |
| Agent visibility runtime command surface | Claude Code는 `/visibility strict|dynamic|minimal`, Codex는 trusted `UserPromptSubmit` hook pseudo-command path for `/visibility`, 모든 platform은 `_shared/agent_visibility_cli.py`를 사용한다. Documentation은 이것들을 하나의 slash-command spelling으로 collapse하면 안 된다 | `scripts.tests.test_install_status_contract` |

## Agent Hook Runtime Contract

Ghost-ALICE installer는 hook file이 있다는 것만으로 충분하다고 보지 않는다. hook payload와 platform permission policy도 current repo contract와 맞아야 한다. 오래된 hook entry가 남아 있어도 runtime payload가 바뀌면 installer는 managed entry를 update한다. hook 존재 여부와 `agent_visibility.profile`은 별개 문제다. profile은 user-facing message surface만 제어할 뿐, missing이나 drift 판정을 누그러뜨리지 않는다.

Base session-gate contract는 core behavior를 설명한다. Official privileged adapter addon은 선언된 hook event 이후 behavior를 확장할 수 있지만, addon-specific queue, task schema, continuation policy는 addon에 남고 addon-owned smoke evidence가 필요하다.

Runtime live smoke는 `docs/ko/policies/live-smoke-regression.md`를 따른다. 이 procedure는 Claude Code, Codex, Antigravity에 같은 README first 10 lines request를 보내 `task-router`, `verification-before-completion`, concise tool-checkpoint failure surface, skill activation permission behavior를 관찰한다.

| Platform | Runtime surface | Contract | Regression owner |
| --- | --- | --- | --- |
| Claude Code | `~/.claude/settings.json` hooks and `permissions.allow` | installed Ghost-ALICE skills 전체에 대해 `Skill(<name>)` permission을 allow한다. `task-router`, `merge-companion`, `verification-before-completion` 같은 core gates만 포함한 shortened allowlist는 insufficient하다 | `_shared.test_install_hooks.TestInstallHook` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, bootstrap `AGENTS.md` | visible Skill tool이 없으므로 required gates는 `SKILL.md` read와 workflow execution으로 기록한다. `hooks.json`의 hook event names는 install-surface configuration이며 runtime firing proof가 아니다. gate-completion claim에는 observed hook payload evidence, runtime smoke, 또는 hookless/manual fallback wording이 필요하다. `web-search-first`와 `tool-checkpoint` payloads는 installation and drift verification targets다. agent visibility profile은 hook execution을 줄이면 안 된다. Windows native Codex는 actual hook payload firing과 `SKILL.md` read records에 별도 smoke를 둔다 | `_shared.test_install_hooks`, `scripts.check_skill_gate_contract`, Windows native live smoke |

## Hook Message Semantics

### task-router

모든 user input은 `task-router` target이다. simple questions, opinions, status comments, follow-up questions, smoke tests 모두 first tool call 전에 route한다. "previous turn에서 이미 routed 됐다"는 valid skip reason이 아니다.

### session-intent-analyzer

모든 user input은 session intent ledger가 observe한다. Hooks는 digest와 event data만 저장하며 raw prompts를 저장하지 않는다. goals, constraints, decisions, non-goals가 바뀌면 agent는 compressed delta를 `intent-state.json`에 기록한다. `intent-state.json`은 update-plus-accumulate state다. scalar intent fields는 newer deltas로 replace되고, list-like constraints, non-goals, questions, criteria, decisions는 stable id 또는 dedupe로 merge된다. 이 context는 `skill-evolution`과 `jailbreak-detector`가 소비한다. deterministic hard-block rules는 explicit attack signals에 대한 narrow regression guards이며 gradual multi-turn jailbreak resistance는 session-intent summary quality와 cumulative constraint comparison에 달려 있다.

### pending merge precheck

SessionStart 또는 UserPromptSubmit hook이 current platform pending-merge precheck를 실행했고 pending warning이 없다는 contract를 제공하면 runtime은 `merge-companion-precheck: clean (hook-verified)`를 기록하고 shell manifest checks를 반복하지 않는다. hook이 undecided entry를 보고하거나 hook evidence가 없을 때만 current platform manifest를 직접 읽는다. undecided entry가 있으면 `merge-companion`을 먼저 surface한다. user-explicit defer/skip은 manifest entry를 `decided=false`로 남긴 채 계속 진행할 수 있다.

### tool-checkpoint recovery surface

Runtime tool-checkpoint payload는 routine recovery cost 또는 recovery note fields를 요구하지 않는다. Default visible surface는 `intent`와 `why`를 유지하고, `failure-mode-if-wrong`과 `recovery-action`은 side effect, forced signal, mismatch, meaningful user decision point, hard-to-recover action에 필요할 때만 나타난다.

### completion-reminder

executed work가 complete, fixed, successful, freshly verified라고 claim하기 전에는 `verification-before-completion`이 lifecycle gate다. Routine explanations, meta-discussion, options는 finished work 또는 verified result를 claim하지 않는 한 이 gate를 요구하지 않는다. Claude Code처럼 visible Skill surface가 있는 환경에서는 actual Skill call 이후에만 `skill-call: verification-before-completion (this turn)`를 사용한다. visible Skill surface가 없는 Codex에서는 current turn에 해당 `SKILL.md`를 실제로 읽고 workflow를 따른 경우에만 같은 record를 사용한다.

final response `[completion-check]`가 `skill-call: verification-before-completion (this turn)`를 claim하면 같은 final response의 `[io-trace]` `skills-loaded`에도 같은 skill이 있어야 한다.

## Shell Matrix

| Environment | Required coverage | Automation |
| --- | --- | --- |
| macOS bash 3.2 | `install.sh` parse와 targeted install/status flows | local macOS full validation |
| modern bash | Linux/GitHub Actions `install.sh` smoke and unittest integration | `installer compatibility matrix` workflow step |
| zsh invocation | `zsh install.sh`는 bash-only syntax 실행 전 bash로 re-exec한다. installer는 bash-owned 상태로 남는다 | `scripts.tests.test_installer_compat_matrix` |
| Linux bash | `install.sh --platform codex --skip-source-health task-router` fixture | `scripts.tests.test_install_preflight_quarantine` |
| WSL | Linux bash와 동일하며 Windows path boundary smoke 포함 | manual 또는 Windows CI |
| Git Bash | copy-mode fallback, Codex hook config through `~/.codex/hooks.json`, UTF-8 bridge | runtime detection and Codex hook config tests |
| Windows native Codex hook smoke | `install.ps1 --platform codex`가 Codex bootstrap, `hooks.json`, `config.toml` hooks enabled를 작성하고 live smoke가 hook payload firing을 관찰한다 | `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestCodexHookSupportGuard`, `docs/ko/policies/live-smoke-regression.md` |
| Windows visibility preference smoke | `install.ps1 -Visibility <profile>`와 `install.cmd -Visibility <profile>`가 `_shared/install_hooks.py --visibility`로 forward된다. `-AgentVisibility`는 compatibility alias로 유지하고 status는 hook state와 runtime profile을 separately report한다 | `scripts.tests.test_install_status_contract`, Windows PowerShell/CMD live smoke |
| Windows PowerShell 5.1 | UTF-8 BOM retained and parser-compatible. analyzer가 있으면 `PSUseCompatibleSyntax` static analysis가 PS5-incompatible syntax를 잡는다 | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_powershell_static_analysis` |
| Windows PowerShell 7.4 LTS cleanup | `install.ps1`가 latest 7.6.x MSI를 resolve하기 전에 product-code scoped `msiexec.exe /x ... /quiet /norestart`로 detected PowerShell 7.4.x MSI products를 제거한다. product code 없는 non-MSI entries는 skip하고, 7.5/7.6+, Windows PowerShell 5.1, unrelated products는 제거하지 않는다 | `scripts.tests.test_install_ps1_pwsh_lts`, `scripts.tests.test_powershell_static_analysis`, Windows native live smoke |
| PowerShell 7 | parser-compatible and install flow accepts Python 3.11+; help output은 official addon alias를 노출하고 `-AddonTag`를 git URL addon source의 branch/tag 선택으로 설명한다 | local/CI PowerShell smoke, `scripts.tests.test_install_cmd_wrapper` |
| CMD wrapper | `install.ps1`에 delegates하고 arguments를 forward하며 UTF-8을 강제한다. official `--addon autopilot` alias는 PowerShell binding과 addon source preparation을 거쳐 유지한다. wrapper-facing help도 같은 alias와 AddonTag wording을 노출한다 | `scripts.tests.test_install_cmd_wrapper` |

## CI Commands

minimum cross-platform Python gate:

```bash
python3 scripts/run_installer_compat_tests.py
```

focused groups:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group installer-runtime-detection
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
python3 scripts/run_installer_compat_tests.py --group installer-status-contract
python3 scripts/run_installer_compat_tests.py --group installer-powershell-static
```

optional PowerShell analyzer gate:

```powershell
Invoke-ScriptAnalyzer -Path ./install.ps1 -Settings ./PSScriptAnalyzerSettings.psd1 -Severity Warning,Error
```
