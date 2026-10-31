# Installer Platform Compatibility Matrix

Language: [🇺🇸 English](../../policies/installer-platform-compatibility-matrix.md) | 🇰🇷 한국어

개발자는 이 계약을 기준으로 Ghost-ALICE 인스톨러의 플랫폼별 호환성을 구현하고, 각 행에 지정된 담당 테스트로 해당 동작을 검증합니다. 책임과 반환 경로는 [Core 아키텍처](../../../architecture_ko.md)를 따릅니다.

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
| Python bootstrap | default install path는 abort 전에 package-manager Python setup을 시도합니다. read-only status/doctor/list는 system을 mutate하지 않습니다 | `scripts.tests.test_install_runtime_detection` |
| Python lookup | `python`, `python3`, `python3.*`, broken store stubs, spaces in PATH | `scripts.tests.test_install_runtime_detection` |
| Encoding | shell entrypoint와 native wrapper에서 UTF-8 mode 강제 | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_install_cmd_wrapper` |
| User home paths | spaces와 non-ASCII HOME fixtures 지원 유지 | `scripts.tests.test_installer_asset_inventory`, installer integration tests |
| PSScriptAnalyzer optional | PSScriptAnalyzer는 optional입니다. module이 없으면 parser/static tests가 minimum gate입니다 | local full validation |
| Public surface parity | README, index.html, Claude command wrappers는 `skill-catalog/skills.json` count, list, targets와 정렬되어야 합니다 | `scripts/validate_public_surfaces.py`, `scripts/run_installer_compat_tests.py --group public-surface-contract` |
| Ghost-ALICE fresh clone install policy | public installs는 fresh `AidALL/ghost-alice` clone plus install을 사용합니다. installer는 existing remotes를 rewrite하거나 local checkout directories를 rename하거나 repository migration flags를 expose하지 않습니다. installed Claude permission cleanup은 managed stale checkout path allow rules로 제한됩니다 | `scripts.tests.test_source_health_gate`, `_shared.test_install_hooks.TestInstallHook` |
| Agent visibility profile | 기본값, 허용 값, `--visibility` 옵션과 `--agent-visibility` 별칭은 [Agent Visibility Profile](../getting-started/installation.md#agent-visibility-profile) 절에 있습니다. native wrapper compatibility alias는 implementation-test coverage로 유지합니다. profile은 generated hook command contracts를 변경하지 않습니다 | `_shared.test_runtime_config`, `_shared.test_hook_profile_gate`, `_shared.test_install_hooks`, `scripts.tests.test_install_status_contract` |
| Agent visibility runtime command surface | 플랫폼별 명령은 [Slash Commands By Platform](../getting-started/installation.md#slash-commands-by-platform) 절에 있습니다. 문서는 `/visibility`를 Codex 명령으로 안내하면 안 됩니다 | `scripts.tests.test_install_status_contract` |

## Agent Hook Runtime Contract

인스톨러는 훅 파일의 존재뿐 아니라 훅 입력·출력과 플랫폼 권한 정책이 현재 저장소 계약과 맞는지도 확인합니다. 기존 훅 항목이 있어도 실행 명령이 달라지면 인스톨러는 관리 항목을 갱신합니다. `agent_visibility.profile`은 훅 누락·불일치 판정을 완화하지 않습니다.

[책임과 흐름](../../../architecture_ko.md#책임과-흐름)이 작업과 판단의 기록·인증을 Core 코드에 배정합니다. 애드온은 선언한 네이티브 이벤트에 관찰 훅을 등록하며, Core 실행기는 그 훅이 호스트를 막지 못하게 합니다. 애드온 Python 코드는 자체 기록을 관리하고, 현재 세션 어시스턴트는 애드온 지침에 따라 추가 작업을 판단하며, 영향받은 애드온 동작의 근거가 필요합니다.

Runtime live smoke는 [live-smoke-regression](live-smoke-regression.md#purpose)을 따르며, 이 절차는 지원을 주장하는 설치 대상마다 작은 요청 하나를 실행하고 관찰할 신호를 나열합니다.

| Platform | Runtime surface | Contract | Regression owner |
| --- | --- | --- | --- |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`, `settings.json` hooks와 `permissions.allow` | `platforms/claude/CLAUDE.md`의 독립적인 관리 블록을 설치하여 임의 작업 디렉터리에서도 공통 계약과 hookless fallback을 제공합니다. 표시가 없는 사용자 파일은 설치 상태로 계산하지 않는 제안 파일로 보존합니다. 모든 설치된 Ghost-ALICE skill에 `Skill(<name>)` 권한을 허용하며 core-only allowlist로 축소하지 않습니다. | `scripts.tests.test_global_rule_blocks`, `scripts.tests.test_validate_entrypoints`, `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestInstallHook` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, bootstrap `AGENTS.md` | visible Skill tool이 없으므로 required gates는 `SKILL.md` read와 workflow execution으로 기록합니다. `hooks.json`의 hook event names는 install-surface configuration이며 runtime firing proof가 아닙니다. gate-completion claim에는 observed hook payload evidence, runtime smoke, 또는 hookless/manual fallback wording이 필요합니다. `tool-checkpoint` payloads는 installation and drift verification targets입니다. 외부 도구 근거 선택은 해당 주장을 검증할 때 verification reference를 따릅니다. Windows native Codex는 actual hook payload firing과 `SKILL.md` read records에 별도 smoke를 둡니다 | `_shared.test_install_hooks`, `scripts.check_skill_gate_contract`, Windows native live smoke |

Codex의 관리 로더는 설치된 `~/.codex/ghost-alice-governance.md`를 공통 지침 소스로 지정합니다. 작업은 그 소스와 프로젝트 고유 제약을 함께 적용하며, 바로 답할 때는 로더의 도구 없는 답변 규칙을 따릅니다. Status와 doctor는 문단 제목과 관계없이 로더가 지정한 공통 파일을 요구합니다. 인스톨러는 다른 프로젝트의 지침 예산을 늘리지 않습니다. 사용자 거버넌스 파일을 보호하며, Codex 공통 계약 경로가 심볼릭 링크이면 교체를 거부합니다. 자세한 내용은 [공통 거버넌스와 프로젝트 지침](../getting-started/installation.md#공통-거버넌스와-프로젝트-지침)을 확인해 주세요.

## Hook Message Semantics

### task-router

입력마다 바로 답할지, 작업으로 task-router를 따를지는 공통 계약의 [Work Selection](../../../platforms/common-governance.md#work-selection) 절이 정합니다.

### session-intent-analyzer

인스톨러는 session-intent-analyzer를 단일 UserPromptSubmit 접수 담당자이자 SessionStart 세션 영수증 훅으로 등록합니다. 접수가 입력을 기록하는 방식은 세션 게이트 매트릭스의 [Session Intent Ledger Contract](session-gate-matrix.md#session-intent-ledger-contract)가 정합니다.

### pending merge precheck

SessionStart 훅의 병합 대기 알림이 뜻하는 것과, 현재 세션 어시스턴트가 매니페스트를 확인하고 그 항목을 검토하는 때는 [merge-companion](../../../merge-companion/SKILL.md#entry-and-activation)이 정합니다.

### tool-checkpoint recovery surface

도구 점검 훅은 일상적인 호출에 복구 비용·메모 필드를 요구하지 않습니다. 보이는 체크포인트 필드는 공통 계약의 io-trace Transparency Rule이 정합니다.

### completion-reminder

설치된 완료 훅은 현재 입력·정확한 답변에 연결된 Core 평가를 사용합니다. Core가 인증하는 두 판단은 [근거와 네이티브 표시](../../../architecture_ko.md#근거와-네이티브-표시)가 설명합니다. Core의 Stop 관찰자가 그 평가를 사용하며 호스트를 막지 않습니다.

Claude는 한 번의 재시도 한도와 미해결 오류 안내를 유지하고 Codex는 실제 완료 근거 오류를 차단합니다. [Verification-before-completion](../../../coding-convention/verification-before-completion/SKILL.md)은 주장 범위와 제어 형식을 담당합니다. [검토 참조](../../../coding-convention/verification-before-completion/references/response-review.md)는 기록 복구와 전달을, [출력 의미](tool-output-semantics.md#검토출력계속-작업)는 반환 상태를 정의합니다. 인스톨러 테스트는 등록된 입력·출력과 플랫폼 연결을 확인하며 모델 판단의 진실성을 판단하지 않습니다.

## Shell Matrix

| Environment | Required coverage | Automation |
| --- | --- | --- |
| macOS bash 3.2 | `install.sh` parse와 targeted install/status flows | local macOS full validation |
| modern bash | Linux/GitHub Actions `install.sh` smoke and unittest integration | `installer compatibility matrix` workflow step |
| zsh invocation | `zsh install.sh`는 bash-only syntax 실행 전 bash로 re-exec합니다. installer는 bash-owned 상태로 남습니다 | `scripts.tests.test_installer_compat_matrix` |
| Linux bash | `install.sh --platform codex --skip-source-health task-router` fixture | `scripts.tests.test_install_preflight_quarantine` |
| WSL | Linux bash와 동일하며 Windows path boundary smoke 포함 | manual 또는 Windows CI |
| Git Bash | copy-mode fallback, Codex hook config through `~/.codex/hooks.json`, UTF-8 bridge | runtime detection and Codex hook config tests |
| Windows native Codex hook smoke | native Windows Codex installer path `.\install.cmd --platform codex`가 Codex bootstrap, `hooks.json`, `config.toml` hooks enabled를 작성하고 live smoke가 hook payload firing을 관찰합니다 | `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestCodexHookSupportGuard`, `docs/ko/policies/live-smoke-regression.md` |
| Windows visibility preference smoke | native wrapper visibility forwarding이 `_shared/install_hooks.py --visibility`에 도달합니다. visibility alias는 compatibility coverage로 계속 허용되고 status는 hook state와 runtime profile을 separately report합니다 | `scripts.tests.test_install_status_contract`, Windows PowerShell/CMD live smoke |
| Windows PowerShell 5.1 | 은퇴했습니다. `install.ps1`은 UTF-8 BOM과 Windows PowerShell 5.1 문법(`PSUseCompatibleSyntax`, 대상 5.1)을 유지해 5.1에서도 첫 문장까지 실행됩니다. 그 문장이 `PowerShell 7.6 or newer is required`와 `winget install --id Microsoft.PowerShell --exact`를 출력하고 종료 코드 1로 끝냅니다 | `scripts.tests.test_thin_shells`, `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_powershell_static_analysis` |
| PowerShell 7 | 기준선은 7.6 LTS 계열입니다. `install.ps1`은 PowerShell 7.6 이상에서 실행되고, 더 낮은 7.x는 같은 요구 문구로 멈춥니다. 인스톨러는 PowerShell을 설치하지 않습니다. 콘솔 입출력을 UTF-8로 설정하고 Python 3.11 이상을 준비한 뒤, 예전 PowerShell 매개변수 이름을 cli 플래그로 바꾸어 모든 인수를 순서대로 `_shared/installer/cli.py`에 넘기고 그 종료 코드를 돌려줍니다 | `scripts.tests.test_thin_shells`(CI 러너처럼 `pwsh`가 있으면 `install.ps1`을 실행), `scripts.tests.test_powershell_static_analysis` |
| CMD wrapper | `pwsh.exe`로, `pwsh.exe`가 없으면 `powershell.exe`로 `install.ps1`을 실행합니다. 후자에서는 `install.ps1`의 버전 확인이 요구 문구를 출력합니다. 인수를 그대로 넘기고 UTF-8을 강제하며 종료 코드를 돌려줍니다. 옵션과 help는 Python 인스톨러가 제공합니다 | `scripts.tests.test_install_cmd_wrapper`, `scripts.tests.test_thin_shells` |

## CI Commands

minimum cross-platform Python gate:

```bash
python3 -m pip install pytest
python3 scripts/run_installer_compat_tests.py
```

`shared-all` 그룹은 shared unittest discovery와 `_shared/test_install_destination.py`, `_shared/test_retired_detector_stage.py`, `_shared/test_retired_discovery.py`의 pytest 함수를 실행합니다. 어느 실행에서든 실패하면 해당 그룹은 실패합니다.

focused groups:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group installer-runtime-detection
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
python3 scripts/run_installer_compat_tests.py --group installer-status-contract
python3 scripts/run_installer_compat_tests.py --group installer-powershell-static
```

optional analyzer coverage는 위 `installer-powershell-static` group을 통해 노출합니다. 직접 analyzer invocation은 public install guidance에 포함하지 않습니다.
