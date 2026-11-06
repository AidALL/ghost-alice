# 인스톨러 플랫폼 호환성 매트릭스

[🇺🇸 English](../../policies/installer-platform-compatibility-matrix.md) | [🇰🇷 한국어](./installer-platform-compatibility-matrix.md)

이 문서는 Ghost-ALICE 인스톨러의 호환성 테스트 계약입니다. 각 행은 요구 동작을 기술하고, 그 동작을 검증하는 테스트나 검증 절차를 지정합니다. 책임과 반환 경로는 [Core 아키텍처](../../../architecture_ko.md)가 설명합니다.

## 목차

- [런타임 계약](#런타임-계약)
- [에이전트 훅 런타임 계약](#에이전트-훅-런타임-계약)
- [훅 메시지의 의미](#훅-메시지의-의미)
  - [task-router](#task-router)
  - [session-intent-analyzer](#session-intent-analyzer)
  - [병합 대기 사전 확인](#병합-대기-사전-확인)
  - [completion-reminder](#completion-reminder)
- [셸 매트릭스](#셸-매트릭스)
- [CI 명령](#ci-명령)


## 런타임 계약

| 영역 | 계약 | 담당 테스트 |
| --- | --- | --- |
| Python 런타임 | Python 3.11+ 허용, 상한 없음, 미래 버전 허용 목록 없음 | `scripts/run_installer_compat_tests.py`, `scripts.tests.test_install_runtime_detection` |
| Python 부트스트랩 | 기본 설치 절차는 중단하기 전에 패키지 관리자로 Python 설치를 시도합니다. 읽기 전용 명령 status·doctor·list는 시스템을 변경하지 않습니다 | `scripts.tests.test_install_runtime_detection` |
| Python 탐색 | `python`, `python3`, `python3.*`, 작동하지 않는 스토어 스텁, 공백이 있는 PATH | `scripts.tests.test_install_runtime_detection` |
| 인코딩 | 셸 진입점과 네이티브 래퍼에 UTF-8 모드 강제 | `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_install_cmd_wrapper` |
| 사용자 홈 경로 | 공백과 ASCII가 아닌 문자를 포함한 HOME 픽스처 지원 유지 | `scripts.tests.test_installer_asset_inventory`, 인스톨러 통합 테스트 |
| PSScriptAnalyzer 선택 사항 | PSScriptAnalyzer는 선택 사항입니다. 모듈이 없으면 파서·정적 테스트가 최소 게이트입니다 | 로컬 전체 검증 |
| 공개 안내 일치 | Claude 명령 래퍼의 대상 경로는 `skill-catalog/skills.json`과 일치해야 합니다. README는 필수 제목과 명령을, index.html은 저장소 루트 기준 링크를 유지해야 합니다 | `scripts/validate_public_surfaces.py`, `scripts/run_installer_compat_tests.py --group public-surface-contract` |
| Ghost-ALICE 새 복제 설치 정책 | 공개 설치는 `AidALL/ghost-alice`를 새로 복제한 뒤 설치합니다. 인스톨러는 설치된 Claude 권한에서 관리 대상인 오래된 체크아웃 경로의 허용 규칙과 더 이상 설치하지 않는 스킬의 `Skill(<name>)` 권한만 삭제합니다. | `_shared.test_install_hooks.TestInstallHook`, `_shared.test_installed_hook_cleanup` |
| 에이전트 표시 프로필 | 기본값, 허용 값, `--visibility` 옵션과 `--agent-visibility` 별칭은 [에이전트 표시 프로필](../getting-started/installation.md#에이전트-표시-프로필) 절에 있습니다. 네이티브 래퍼의 호환 별칭은 구현 테스트가 계속 검증합니다. 프로필은 생성된 훅 명령 계약을 변경하지 않습니다. | `_shared.test_runtime_config`, `_shared.test_hook_profile_gate`, `_shared.test_install_hooks`, `scripts.tests.test_install_status_contract` |
| 에이전트 표시 런타임 명령 | 플랫폼별 명령은 [플랫폼별 슬래시 명령](../getting-started/installation.md#플랫폼별-슬래시-명령) 절에 있습니다. 문서는 `/visibility`를 Codex 명령으로 안내하면 안 됩니다 | `scripts.tests.test_install_status_contract` |

## 에이전트 훅 런타임 계약

인스톨러는 훅 파일이 있다는 사실만으로 충분하다고 판단하지 않습니다. 인스톨러는 관리 훅 항목과 플랫폼 권한 정책도 현재 저장소 계약과 동기화합니다. 기존 관리 훅 항목의 명령이 인스톨러가 생성하는 명령과 다르면 인스톨러는 그 항목을 교체합니다. `agent_visibility.profile`은 훅 누락·불일치 판정을 완화하지 않습니다.

[책임과 흐름](../../../architecture_ko.md#책임과-흐름)이 작업과 판단의 기록·인증을 Core 코드에 배정합니다. 애드온은 선언한 네이티브 이벤트에 관찰 훅을 등록하며, Core 실행기는 그 훅이 호스트를 막지 못하게 합니다.

실환경 점검은 [실환경 점검 회귀 테스트](live-smoke-regression.md#목적)를 따릅니다. 이 절차는 지원을 주장하는 설치 대상마다 작은 요청 하나를 실행하고 관찰할 신호를 나열합니다.

| 플랫폼 | 런타임 설치 항목 | 계약 | 담당 회귀 테스트 |
| --- | --- | --- | --- |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`, `settings.json` 훅과 `permissions.allow` | `platforms/claude/CLAUDE.md`의 독립적인 관리 블록을 설치하여 임의 작업 디렉터리에서도 공통 계약과 훅 없는 대체 절차를 제공합니다. 관리 표지가 없는 사용자 파일은 변경하지 않고, 설치 상태에 포함하지 않는 제안 파일을 따로 작성합니다. 설치된 Ghost-ALICE 스킬 전체에 `Skill(<name>)` 권한을 허용하며, Core 검사 스킬만 담은 축소된 허용 목록으로는 부족합니다. | `scripts.tests.test_global_rule_blocks`, `scripts.tests.test_validate_entrypoints`, `scripts.tests.test_install_status_contract`, `_shared.test_install_hooks.TestInstallHook` |
| Codex | `~/.codex/hooks.json`, `~/.codex/config.toml`, 부트스트랩 `AGENTS.md` | 모델에 노출된 Skill 도구가 없으므로 필수 게이트는 `SKILL.md` 조회와 워크플로 실행으로 기록합니다. `hooks.json`의 훅 이벤트 이름은 설치된 설정이며 런타임 호출의 증거가 아닙니다. 게이트 완료 주장은 관찰된 훅 입력·출력 근거, 실환경 점검, 또는 훅 없는 대체 절차나 수동 대체 절차의 문구를 요구합니다. `tool-checkpoint` 훅 항목은 설치·불일치 검증 대상입니다. 주장이 외부 도구 근거를 요구하면 검증 참조에 따라 그 근거를 선택합니다. Windows 네이티브 Codex에서는 별도 실환경 점검이 실제 훅 호출의 입력·출력과 `SKILL.md` 조회 기록을 확인합니다. | `_shared.test_install_hooks`, `scripts.check_skill_gate_contract`, Windows 네이티브 실환경 점검 |

Codex의 관리 로더는 설치된 `~/.codex/ghost-alice-governance.md`를 공통 지침 소스로 지정합니다. 작업은 그 소스와 프로젝트 고유 제약을 함께 적용하며, 바로 답할 때는 로더의 도구 없는 답변 규칙을 따릅니다. Status와 doctor는 문단 제목과 관계없이 로더가 지정한 공통 파일을 요구합니다. 인스톨러는 다른 프로젝트의 지침 예산을 늘리지 않습니다. 사용자 거버넌스 파일을 보호하며, Codex 공통 계약 경로가 심볼릭 링크이면 교체를 거부합니다. 자세한 내용은 [공통 거버넌스와 프로젝트 지침](../getting-started/installation.md#공통-거버넌스와-프로젝트-지침)을 확인해 주세요.

## 훅 메시지의 의미

### task-router

입력마다 바로 답할지, 작업으로 task-router를 따를지는 공통 계약의 [Work Selection](../../../platforms/common-governance.md#work-selection) 절이 정합니다.

### session-intent-analyzer

인스톨러는 session-intent-analyzer를 단일 UserPromptSubmit 접수 담당자이자 SessionStart 세션 영수증 훅으로 등록합니다. 접수가 입력을 기록하는 방식은 세션 게이트 매트릭스의 [세션 의도 원장 계약](session-gate-matrix.md#세션-의도-원장-계약)이 정합니다.

### 병합 대기 사전 확인

SessionStart 훅의 병합 대기 알림이 뜻하는 것과, 응답 모델이 매니페스트를 확인하고 그 항목을 검토하는 시점은 [merge-companion](../../../merge-companion/SKILL.md#entry-and-activation)이 정합니다.

### completion-reminder

Core 코드의 Stop 스크립트는 [훅 호출](../../../architecture_ko.md#훅-호출)이, Core가 인증하는 두 판단은 [근거와 네이티브 표시](../../../architecture_ko.md#근거와-네이티브-표시)가 설명합니다.

훅 실행기는 실행하는 모든 훅의 JSON 출력에서 차단·정지·권한 결정 제어를 제거하고 차단·정지·거부·사용자 확인 요청을 `systemMessage`로 알리며, 훅의 종료 코드 2를 차단하지 않는 종료 코드 1로 변환합니다. 훅 로그는 원래 종료 코드를 보존합니다. [Verification-before-completion](../../../coding-convention/verification-before-completion/SKILL.md)은 주장 범위와 완료 확인 블록의 형식을 정합니다. [검토 참조](../../../coding-convention/verification-before-completion/references/response-review.md)는 기록 복구와 전달을, [출력 의미](tool-output-semantics.md#검토출력계속-작업)는 반환 상태를 정의합니다. 인스톨러 테스트는 등록된 훅 항목과 플랫폼 연결을 확인할 뿐 모델 판단이 참인지 검증하지 않습니다.

## 셸 매트릭스

| 환경 | 필수 검증 범위 | 자동화 |
| --- | --- | --- |
| macOS bash 3.2 | `install.sh` 구문 분석과 대상을 지정한 install/status 실행 | 로컬 macOS 전체 검증 |
| 최신 bash | Linux/GitHub Actions의 `install.sh` 점검과 unittest 통합 테스트 | `ci.yml`의 `Public validation` 작업 |
| zsh 호출 | `zsh install.sh`는 bash 전용 문법을 실행하기 전에 bash로 자신을 다시 실행하고, 그다음 Python 인스톨러 `_shared/installer/cli.py`를 실행합니다 | `scripts.tests.test_installer_compat_matrix` |
| Linux bash | `install.sh --platform codex --skip-source-health task-router` 픽스처 | `scripts.tests.test_install_preflight_quarantine` |
| WSL | Linux bash와 동일하며 Windows 경로 경계 점검 포함 | 수동 또는 Windows CI |
| Git Bash | `copy-fallback` 설치 방식, `~/.codex/hooks.json`을 통한 Codex 훅 설정, UTF-8 로케일과 Python UTF-8 모드 강제 | 런타임 감지와 Codex 훅 설정 테스트 |
| Windows 네이티브 Codex 훅 점검 | 네이티브 Windows Codex 인스톨러 명령 `.\install.cmd --platform codex`가 Codex 부트스트랩, `hooks.json`, 훅을 활성화한 `config.toml`을 작성하고, 실환경 점검이 훅 호출의 입력·출력을 관찰합니다 | `scripts.tests.test_install_status_contract`, `docs/ko/policies/live-smoke-regression.md` |
| Windows 표시 설정 점검 | 네이티브 래퍼가 표시 설정 인수를 `_shared/install_hooks.py --visibility`까지 전달합니다. 호환용 표시 설정 별칭도 계속 허용하며, status는 훅 상태와 런타임 프로필을 따로 출력합니다 | `scripts.tests.test_install_status_contract`, Windows PowerShell/CMD 실환경 점검 |
| Windows PowerShell 5.1 | 지원하지 않습니다. `install.ps1`은 UTF-8 BOM과 Windows PowerShell 5.1 문법(`PSUseCompatibleSyntax`, 대상 5.1)을 유지해 5.1에서도 첫 문장까지 실행됩니다. 그 문장이 `PowerShell 7.6 or newer is required`와 `winget install --id Microsoft.PowerShell --exact`를 출력하고 종료 코드 1로 끝냅니다 | `scripts.tests.test_thin_shells`, `scripts.tests.test_install_ps1_encoding`, `scripts.tests.test_powershell_static_analysis` |
| PowerShell 7 | 기준선은 7.6 LTS 계열입니다. `install.ps1`은 PowerShell 7.6 이상에서 실행되고, 더 낮은 7.x는 같은 요구 문구로 멈춥니다. 인스톨러는 PowerShell을 설치하지 않습니다. 콘솔 입출력을 UTF-8로 설정하고 Python 3.11 이상을 준비한 뒤, 예전 PowerShell 매개변수 이름을 cli 플래그로 바꾸어 모든 인수를 순서대로 `_shared/installer/cli.py`에 넘기고 그 종료 코드를 돌려줍니다 | `scripts.tests.test_thin_shells`(CI 러너처럼 `pwsh`가 있으면 `install.ps1`을 실행), `scripts.tests.test_powershell_static_analysis` |
| CMD 래퍼 | `pwsh.exe`로, `pwsh.exe`가 없으면 `powershell.exe`로 `install.ps1`을 실행합니다. 후자에서는 `install.ps1`의 버전 확인이 요구 버전 문구를 출력합니다. 인수를 변경 없이 전달하고 UTF-8을 강제하며 종료 코드를 반환합니다. 옵션과 도움말은 Python 인스톨러가 제공합니다 | `scripts.tests.test_install_cmd_wrapper`, `scripts.tests.test_thin_shells` |

## CI 명령

플랫폼 공통 최소 Python 게이트:

```bash
python3 -m pip install pytest
python3 scripts/run_installer_compat_tests.py
```

`shared-all` 그룹은 `_shared/`에서 unittest 자동 탐색으로 찾은 테스트와 `_shared/test_install_destination.py`의 pytest 함수를 실행합니다. 두 실행 중 하나라도 실패하면 그룹이 실패합니다.

`scripts/run_installer_compat_tests.py`를 `--group` 없이 실행하면, 이 스크립트는 `within` 필드에 다른 그룹을 지정한 개별 그룹을 건너뜁니다. `within`에 지정된 그룹이 그 개별 그룹의 테스트 모듈을 모두 실행하므로, 각 테스트 모듈은 한 번만 실행됩니다. 개별 그룹은 `--group`으로 이름을 지정해 따로 실행합니다.

개별 그룹:

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group installer-runtime-detection
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
python3 scripts/run_installer_compat_tests.py --group installer-status-contract
python3 scripts/run_installer_compat_tests.py --group installer-powershell-static
```

선택 사항인 분석기 검사는 위 `installer-powershell-static` 그룹이 제공합니다. 분석기 직접 호출은 의도적으로 공개 설치 안내에 포함하지 않습니다.
