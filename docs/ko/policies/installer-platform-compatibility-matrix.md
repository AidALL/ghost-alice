# Installer Platform Compatibility Matrix

Language: [🇺🇸 English](../../policies/installer-platform-compatibility-matrix.md) | 🇰🇷 한국어

개발자는 이 계약을 기준으로 Ghost-ALICE 설치기의 플랫폼별 호환성을 구현하고, 각 행에 지정된 담당 테스트로 해당 동작을 검증합니다. 단독 실행의 책임과 반환 경로는 [Core 아키텍처](../../../architecture_ko.md), 애드온 확장은 [Autopilot 아키텍처](https://github.com/AidALL/ghost-alice-autopilot/blob/main/README_ko.md#autopilot-설치로-확장되는-core-흐름)를 따릅니다.

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
| Public surface parity | README, docs/index.html, Claude command wrappers는 `skill-catalog/skills.json` count, list, targets와 정렬되어야 합니다 | `scripts/validate_public_surfaces.py`, `scripts/run_installer_compat_tests.py --group public-surface-contract` |
| Ghost-ALICE fresh clone install policy | public installs는 fresh `AidALL/ghost-alice` clone plus install을 사용합니다. installer는 existing remotes를 rewrite하거나 local checkout directories를 rename하거나 repository migration flags를 expose하지 않습니다. installed Claude permission cleanup은 managed stale checkout path allow rules로 제한됩니다 | `scripts.tests.test_source_health_gate`, `_shared.test_install_hooks.TestInstallHook` |
| Agent visibility profile | default `agent_visibility.profile`은 `dynamic`입니다. allowed values는 `strict`, `dynamic`, `minimal`입니다. public installer command surface는 `--visibility`를 사용합니다. native wrapper compatibility alias는 implementation-test coverage로 유지합니다. `--agent-visibility`는 long-form compatibility alias로 계속 허용됩니다. profile은 user-facing governance message surface만 제어합니다. hook installation/execution을 suppress하지 않고 generated hook command contracts를 바꾸지 않으며 strict-grade session logging은 always-on입니다 | `_shared.test_runtime_config`, `_shared.test_hook_profile_gate`, `_shared.test_install_hooks`, `scripts.tests.test_install_status_contract` |
| Agent visibility runtime command surface | Claude Code는 `/visibility strict|dynamic|minimal`, Codex는 trusted `UserPromptSubmit` hook pseudo-command path for `/visibility`, 모든 platform은 `_shared/agent_visibility_cli.py`를 사용합니다. Documentation은 이것들을 하나의 slash-command spelling으로 collapse하면 안 됩니다 | `scripts.tests.test_install_status_contract` |

## Agent Hook Runtime Contract

설치기는 훅 파일의 존재뿐 아니라 훅 입력·출력과 플랫폼 권한 정책이 현재 저장소 계약과 맞는지도 확인합니다. 기존 훅 항목이 있어도 실행 명령이 달라지면 설치기는 관리 항목을 갱신합니다. `agent_visibility.profile`은 사용자에게 보이는 메시지만 제어하며 훅 누락·불일치 판정을 완화하지 않습니다.

Core Python 코드는 승인된 작업 단위·기준을 저장하고 현재 세션 어시스턴트의 주장·근거 판단을 인증합니다. 공식 privileged adapter는 선언된 네이티브 이벤트에서 이 계약을 소비합니다. 애드온 Python 코드는 불변 기록·영수증 스키마를 관리하고, 현재 세션 어시스턴트는 애드온 지침에 따라 추가 작업을 판단하며, 영향받은 애드온 동작의 근거가 필요합니다.

Runtime live smoke는 `docs/ko/policies/live-smoke-regression.md`를 따릅니다. 이 procedure는 지원할 설치된 Claude Code·Codex 대상에 같은 README first 10 lines request를 보내 `task-router`, `verification-before-completion`, concise tool-checkpoint failure surface, skill activation permission behavior를 관찰합니다.

| Platform | Runtime surface | Contract | Regression owner |
| --- | --- | --- | --- |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`, `settings.json` hooks와 `permissions.allow` | `platforms/claude/CLAUDE.md`의 독립적인 관리 블록을 설치하여 임의 작업 디렉터리에서도 공통 계약과 hookless fallback을 제공합니다. 표시가 없는 사용자 파일은 설치 상태로 계산하지 않는 제안 파일로 보존합니다. 모든 설치된 Ghost-ALICE skill에 `Skill(<name>)` 권한을 허용하며 core-only allowlist로 축소하지 않습니다. | `scripts.tests.test_global_rule_blocks`, `scripts.tests.test_validate_entrypoints`, `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestInstallHook` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, bootstrap `AGENTS.md` | visible Skill tool이 없으므로 required gates는 `SKILL.md` read와 workflow execution으로 기록합니다. `hooks.json`의 hook event names는 install-surface configuration이며 runtime firing proof가 아닙니다. gate-completion claim에는 observed hook payload evidence, runtime smoke, 또는 hookless/manual fallback wording이 필요합니다. `tool-checkpoint` payloads는 installation and drift verification targets입니다. 외부 도구 근거 선택은 해당 주장을 검증할 때 verification reference를 따릅니다. agent visibility profile은 hook execution을 줄이면 안 됩니다. Windows native Codex는 actual hook payload firing과 `SKILL.md` read records에 별도 smoke를 둡니다 | `_shared.test_install_hooks`, `scripts.check_skill_gate_contract`, Windows native live smoke |

Codex의 관리 로더는 설치된 `~/.codex/ghost-alice-governance.md`를 공통 지침 소스로 지정합니다. 작업은 그 소스와 프로젝트 고유 제약을 함께 적용하며, 바로 답할 때는 로더의 도구 없는 답변 규칙을 따릅니다. Status와 doctor는 문단 제목과 관계없이 로더가 지정한 공통 파일을 요구합니다. 설치기는 다른 프로젝트의 지침 예산을 늘리지 않습니다. 사용자 거버넌스 파일을 보호하며, Codex 공통 계약 경로가 심볼릭 링크이면 교체를 거부합니다. 자세한 내용은 [공통 거버넌스와 프로젝트 지침](../getting-started/installation.md#공통-거버넌스와-프로젝트-지침)을 확인해 주세요.

## Hook Message Semantics

### task-router

현재 세션 어시스턴트는 모든 사용자 입력을 `task-router`에 연결합니다. 단순 질문, 의견, 상태 확인, 후속 질문과 실행 점검도 첫 도구 호출 전에 라우팅합니다. 이전 턴에서 이미 라우팅했다는 이유로 현재 입력의 라우팅을 생략하지 않습니다.

### session-intent-analyzer

설치기는 session-intent-analyzer를 단일 UserPromptSubmit 접수 담당자이자 SessionStart 세션 영수증 훅으로 등록합니다. Codex는 직접 확정하며 Claude는 신뢰된 PreToolUse/Stop 대조를 위해 네이티브 연결 정보를 보관합니다. 설치된 래퍼는 정확한 기준 사용자 입력을 반환하거나 pending을 유지하며, 작업 알림은 마지막 사용자 경로를 보존합니다. [접수 절차](../../../session-intent-analyzer/SKILL.md)와 [원장 스키마](../../../session-intent-analyzer/references/ledger-schema.md)가 식별자·압축 변경·저장·호환성 내보내기를 담당합니다.

### pending merge precheck

SessionStart 훅은 세션 시작 때 현재 플랫폼의 병합 대기 매니페스트를 확인하고 결과를 알립니다. 선택된 작업이 설치된 스킬을 바꾸기 전에는, 세션 중 설치기 실행으로 항목이 생길 수 있으므로 현재 세션 어시스턴트가 매니페스트를 다시 확인합니다. 미결 항목이 있으면, 사용자가 검토를 요청하거나 선택된 작업이 영향받는 스킬을 바꿀 때 `merge-companion`을 적용합니다. 사용자가 명시적으로 미루거나 건너뛰면 해당 항목은 `decided=false`로 유지한 채 다른 승인 작업을 진행할 수 있습니다.

### tool-checkpoint recovery surface

도구 점검 훅은 일상적인 호출에 복구 비용·메모 필드를 요구하지 않습니다. 보이는 체크포인트 필드는 공통 계약의 io-trace Transparency Rule이 정합니다.

### completion-reminder

설치된 완료 훅은 현재 입력·정확한 답변에 연결된 Core 평가를 사용합니다. Core는 모델의 서로 다른 주장·출처 판단과 응답 전체 판단을 인증하며, 스킬 로드나 화면 감사 필드는 별도의 수락 조건이 아닙니다. 독립 등록된 Core 가드와 Ghost-ALICE Autopilot 어댑터는 호출 순서를 가정하지 않고 같은 평가를 사용해야 합니다.

Claude는 한 번의 재시도 한도와 미해결 오류 안내를 유지하고 Codex는 실제 완료 근거 오류를 차단합니다. [Verification-before-completion](../../../coding-convention/verification-before-completion/SKILL.md)은 주장 범위와 제어 형식을 담당합니다. [검토·접수 참조](../../../coding-convention/verification-before-completion/references/autopilot-publication.md)는 기록 복구와 전달을, [출력 의미](tool-output-semantics.md#검토출력계속-작업)는 반환 상태를 정의합니다. 설치기 테스트는 등록된 입력·출력과 플랫폼 연결을 확인하며 모델 판단의 진실성을 판단하지 않습니다.

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
| Windows PowerShell 5.1 | UTF-8 BOM retained and parser-compatible. analyzer가 있으면 `PSUseCompatibleSyntax` static analysis가 PS5-incompatible syntax를 잡습니다 | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_powershell_static_analysis` |
| Windows PowerShell 7.4 LTS cleanup | native Windows installer cleanup이 latest 7.6.x MSI를 resolve하기 전에 product-code scoped `msiexec.exe /x ... /quiet /norestart`로 detected PowerShell 7.4.x MSI products를 제거합니다. product code 없는 non-MSI entries는 skip하고, 7.5/7.6+, Windows PowerShell 5.1, unrelated products는 제거하지 않습니다 | `scripts.tests.test_install_ps1_pwsh_lts`, `scripts.tests.test_powershell_static_analysis`, Windows native live smoke |
| PowerShell 7 | parser-compatible and install flow accepts Python 3.11+; help output은 official addon alias를 노출하고 git URL addon source용 native wrapper branch/tag option을 설명합니다 | local/CI PowerShell smoke, `scripts.tests.test_install_cmd_wrapper` |
| CMD wrapper | native Windows installer entrypoint에 delegates하고 arguments를 forward하며 UTF-8을 강제합니다. official `--addon autopilot` alias는 PowerShell binding과 addon source preparation을 거쳐 유지합니다. wrapper-facing help도 같은 alias와 branch/tag wording을 노출합니다 | `scripts.tests.test_install_cmd_wrapper` |

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
