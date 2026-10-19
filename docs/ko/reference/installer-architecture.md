# Installer Architecture

Language: [🇺🇸 English](../../reference/installer-architecture.md) | 🇰🇷 한국어

이 문서는 기여자가 빠른 설치 안내에서 설치기 호환성 계약으로 넘어갈 때 사용하는 구조 안내입니다. 설치기, 훅 입력·출력, 설치 상태와 테스트 사이의 연결을 설명합니다.

사용자는 [설치 안내](../getting-started/installation.md)에서 실행 절차를 확인하실 수 있습니다. 기여자는 [플랫폼 호환성 계약](../policies/installer-platform-compatibility-matrix.md)에서 요구 조건과 담당 테스트를 확인합니다.
## Contents

- [읽는 순서](#읽는-순서)
- [Architecture Summary](#architecture-summary)
- [Install Flow](#install-flow)
- [Gateway Lifecycle](#gateway-lifecycle)
- [Platform Surfaces](#platform-surfaces)
- [State And Safety Model](#state-and-safety-model)
- [Contributor Entry Points](#contributor-entry-points)
- [What Not To Infer](#what-not-to-infer)
- [Full Understanding](#full-understanding)


## 읽는 순서

1. 이 문서에서 구성 요소와 호출 방향을 파악합니다.
2. 파일 위치는 [repository structure](./repository-structure.md)에서 찾습니다.
3. 사용자에게 보이는 동작은 [installation](../getting-started/installation.md)과 [uninstall](../getting-started/uninstall.md)에서 확인합니다.
4. 계약과 필요한 검증은 [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md), [tool output semantics](../policies/tool-output-semantics.md), [live smoke regression](../policies/live-smoke-regression.md)에서 확인합니다.

## Architecture Summary

설치기 구성 요소는 다음 세 계층에서 역할을 나눕니다.

| Layer | Main files | Responsibility |
| --- | --- | --- |
| Shell entrypoints | `install.sh`, `install.ps1`, `install.cmd` | `install.sh`는 UTF-8 로케일을 설정하고 Python 3.11 이상을 찾거나 설치한 뒤, 사용자의 인수를 그대로 `_shared/installer/cli.py`에 넘깁니다. `install.ps1`은 얇은 판이 나올 때까지 PowerShell 설치기를 그대로 담습니다 |
| Installer package | `_shared/installer/` (`cli`, `auto`, `selection`, `addons`, `preservation`, `transaction`, `hosts`, `diagnostics`, `lifecycle`, `source`, `prerequisites`, `lock`, `report`, `locations`, `config`, `runner`) | 옵션을 해석하고 명령을 고르며 플랫폼과 대상을 정합니다. 설치 단계 순서를 지키고 설치 잠금을 쥐며 보고서를 출력합니다. 파일 변경은 모두 아래 진입점을 거칩니다 |
| Installer entry points | `_shared/install_hooks.py`, `_shared/install_transaction.py`, `_shared/install_state_writer.py`, `_shared/addon_installer.py`, `_shared/uninstall_cleanup.py`, `_shared/install_doctor.py`와 패키지가 자식 프로세스로 실행하는 다른 `_shared/*.py` 도구 | 스킬과 훅을 설치하고 설치 상태를 기록합니다. 로컬 수정을 보호하고 관리 대상을 제거하며 설치를 진단합니다 |
| Runtime surfaces | `~/.claude/`, `~/.codex/`, `~/.agents/skills/`, `~/.ghost-alice/` | 설치된 스킬, 플랫폼 훅 설정, Claude·Codex 전역 부트스트랩 지침, 설치 상태 매니페스트, 훅 기능 복원 메타데이터, 병합 대기 자료와 제거 결과를 보관합니다 |

설치기는 설치 파일과 훅을 갱신하면서 사용자가 수정한 파일을 보호하고 자신의 변경을 기록합니다. `status`, `doctor`, 업데이트와 제거 절차는 이 기록을 읽어 실제 설치 상태와 복원 가능한 변경을 판단합니다.

설치된 훅 런타임은 같은 런타임 루트 아래에 `_shared` 모듈과 `session-intent-analyzer/scripts/session_intent_ledger.py`를 함께 포함합니다. 네이티브 훅과 Ghost-ALICE Autopilot은 이 설치본에서 원장 API와 관련 모듈을 불러옵니다. 소스 체크아웃은 설치 입력을 지정하며, 소스 편집만으로 설치된 훅의 실행 모듈이 바뀌지 않습니다. 원장 데이터베이스 경로와 네이티브 세션 식별자는 별도의 실행 입력입니다.

`_shared/hook_definitions.py`는 설치기와 훅 런타임이 함께 읽는 이름을 소유합니다. 훅 ID와 호스트 이벤트 이름, `ghost_alice_context` 제공자 이름, 권한 어댑터 허용 목록이 여기에 있습니다. 이 모듈은 표준 라이브러리만 가져옵니다. 훅 런타임(`hook_profile_gate.py`, `session_intent_analyzer_hook.py`)과 설치기(`install_hooks.py`, `addon_installer.py`, `context_gateway_*` 모듈, `installer` 패키지)가 이 모듈을 가져옵니다. 런타임 모듈은 설치기 모듈을 가져오지 않고, 설치기 모듈은 런타임 모듈을 가져오지 않습니다. `_shared.test_hook_definitions`가 두 규칙을 검사하고, `installer` 패키지의 모듈 이름이 표준 라이브러리 모듈 이름과 겹치지 않는지도 검사합니다.

## Install Flow

| 전달 주체 | 입력으로 넘기는 내용 | 다음 단계에서 사용하는 결과 |
| --- | --- | --- |
| 사용자 명령 → 셸 진입점 | 입력한 명령줄 그대로. | 사용 가능한 Python 3.11 이상 실행 환경과, 바뀌지 않은 인수. |
| 진입점 → `installer.cli` | 인수와 셸이 놓인 소스 체크아웃. | 해석한 옵션, 선택한 명령, 설치라면 플랫폼·스킬 디렉터리·적용 가시성. |
| `installer.cli` → `installer.selection`·`installer.addons` | 요청한 스킬 이름과 애드온 출처. | 정확한 설치 대상(핵심 스킬, `_shared`, 애드온 대상)과 대상 수. |
| 조정 코드 → 플랫폼 배포 | 소스 파일과 대상 경로. | 설치한 스킬·도우미와 보존한 로컬 수정. |
| 배포 → 훅·부트스트랩 동기화 | 관리 파일 내용, 플랫폼 경로, 등록 명령. | 갱신한 관리 규칙 블록과 훅 등록. |
| 설치기 → 설치 상태 기록기 → 상태 출력 | 실제 변경 대상·해시·복원 정보. | Status·doctor·제거에서 읽을 플랫폼 매니페스트와 설치 보고서. |

Windows에서는 `install.cmd`가 인수를 `install.ps1`에 전달하고 실제 설치는 `install.ps1`이 담당합니다. Unix 계열 환경에서는 `install.sh`가 셸 진입점을 담당하며 필요할 때 Bash로 다시 실행합니다. `--help`와 `--list`를 포함한 모든 명령은 Python에서 실행됩니다. 셸은 설치기 문구의 사본을 갖지 않습니다.

### 설치 단계 순서

`installer.cli.run_install`은 설치를 다음 순서로 진행합니다. `_shared/test_installer_cli.py`가 이 순서를 고정합니다.

1. 선행 도구(`git`, `node`)와 소스 체크아웃의 git hook 경로.
2. 설치 잠금(`~/.ghost-alice/install.lock`). 명령이 끝날 때까지 쥡니다.
3. 소스 상태 확인, 그다음 대상 수집(핵심 스킬, `_shared`, 애드온 대상).
4. deprecated 설치 스킬을 발견 경로 밖으로 옮긴 뒤, 인코딩 검사, 그다음 로컬 수정을 격리하거나 기록하는 사전 점검.
5. 중단된 애드온 제거를 재개합니다. 애드온 충돌은 설치를 멈춥니다.
6. 배치: `_shared`와 모든 대상을 링크(Claude Code)로, 또는 하나의 복사 배치(Codex)로 `install_transaction.py`를 통해 놓습니다.
7. 런타임 `_shared` 동기화, 훅 설치, 관리 규칙 블록, 명령 래퍼.
8. 검증, 소유 표지, merge-companion 스냅숏, 애드온 사이드카, 설치 상태 매니페스트, 설치 이벤트.

제거, 복구(`--repair`), `--cleanup-pending`, `gateway` 전달 명령도 잠금 아래에서 실행됩니다. 게이트웨이를 조정하는 자식 도구는 자신을 호출한 설치기 프로세스의 잠금을 그대로 씁니다. 잠금 파일이 그 프로세스 ID를 기록하며, 무관한 소유자는 도구를 막습니다.

## Gateway Lifecycle

게이트웨이 설치·갱신·해제는 `_shared/context_gateway_*`의 여섯 모듈이 담당합니다. `model`은 요청·관측값·계획·결과와 실행 인터페이스의 자료형을 정의하며 AI 모델을 호출하지 않습니다. `inventory`는 전달받은 플랫폼 어댑터로 사실을 읽습니다. `plan`은 소유권을 확인하고 변경 대상과 실제 게이트웨이 의존성을 비교하여 I/O 없이 순서가 정해진 계획을 만듭니다. `transaction`은 계획 실행과 역순 복구를 담당합니다. `platform`은 개별 파일·네이티브 서비스 작업을 수행합니다. `install`은 기존 Python·CLI 진입점에서 이 모듈들을 연결합니다.

일반 파일 교체는 `install_transaction.py`의 되돌릴 수 있는 `ReplacementBatch` 작업을, 관리형 Git 업데이트는 `installer_source_update.py`의 되돌릴 수 있는 `SourceUpdate` 작업을 제공합니다. 선택한 경로가 소유권이 확인된 게이트웨이에 영향을 주면 같은 게이트웨이 트랜잭션이 교체 전에 서비스를 중지하고 후보 서비스의 준비 상태를 확인한 뒤 설치 명세와 등록 바인딩을 갱신합니다. 무관함이 확인된 변경은 게이트웨이를 그대로 둡니다. Core 정리는 영향을 받는 게이트웨이를 먼저 해제한 뒤 의존 파일을 삭제합니다. 소유권이나 의존성을 확인할 수 없으면 해제와 그에 의존하는 삭제를 허용하지 않습니다.

게이트웨이 설치 CLI의 `--dry-run` 옵션은 확인한 변경 계획을 출력하고 실제 반영이나 승인 대기 없이 종료합니다. 게이트웨이·컴포넌트 오류는 발생 단계와 고정 오류 식별자를 유지합니다. 종료 코드 2는 선행 조건 불충족, 3은 활성 파일 교체 전 서비스 상태 확인 불가, 4는 작업 실패 후 이전 설치 복원, 5는 변경 시작 후 복구 확인 불가를 뜻합니다. 일반 셸·PowerShell 호출자는 기존 전체 실패 보고와 구조화된 컴포넌트 원인을 유지합니다. 설치에는 zstd 코덱이 동작하는 Python 3.14가 필요하며 해제는 Python 3.11을 지원합니다. [설치 생명주기 명세](../../coding-convention/specs/2026-10-15-gateway-installer-design.md)에서 작업별 조건, 자료 전달과 실패 분기를 확인하실 수 있습니다.

## Platform Surfaces

| Platform | Installed surfaces |
| --- | --- |
| Claude Code | `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/`, `${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md`, `${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`, Claude command wrappers |
| Codex | `~/.agents/skills/`, `~/.codex/AGENTS.md`, `~/.codex/ghost-alice-governance.md`, `~/.codex/hooks.json`, `~/.codex/config.toml` |
| Shared state | `~/.ghost-alice/install-state/`, `~/.ghost-alice/pending-merges/`, `~/.ghost-alice/uninstall-reports/`, `~/.ghost-alice/install/` |

Claude Code의 native skill invocation과 hook permission은 저장소 밖의 세션에 필요한 global instruction을 대신하지 않습니다. installer는 `platforms/claude/CLAUDE.md`의 managed block을 Claude config directory에 병합합니다. Codex의 별도 bootstrap과 hook config는 required gate를 `SKILL.md` read record와 hook payload로 감사할 수 있게 합니다. 두 global rule file은 managed block 밖의 사용자 텍스트를 보존하며, marker가 없는 사용자 파일에는 덮어쓰기 대신 `.ghost-alice-proposed` 제안 파일을 만듭니다.

일반 Core hook 전달은 실제 native cwd를 Core 맥락으로 유지하고 명시된 Ghost-ALICE Autopilot 대상 override를 보존합니다. 프로젝트 선택은 Ghost-ALICE Autopilot이 담당합니다. Claude는 절대 CLI 프로젝트 경로를 payload cwd보다 먼저 선택하며, Codex는 payload cwd를 사용하고 Claude 전용 힌트를 무시합니다. 설치 source와 프로젝트 신뢰 경로는 Ghost-ALICE Autopilot 대상을 선택하지 않으며, 격리 바인딩은 소유 프로젝트 대상을 유지합니다. Pretool과 Stop은 해당 대상을 선택하고 OFF를 읽은 뒤 Core 권한 자료에 접근합니다. OFF는 캡처 맥락이나 권한 읽기·쓰기 없이 조용히 계속 진행을 반환하며, 활성 대상은 기존 적격 선택 검사로 진행합니다. 대상 선택은 권한을 부여하지 않습니다.

관리 훅 명령과 네이티브 신뢰 항목은 검토한 설치 내용과 일치해야 합니다. 실행 중인 호스트가 이전 등록을 유지할 수 있으므로 적절히 재로드한 뒤 실제 네이티브 콜백으로 반영을 확인합니다. [훅 신뢰와 재로드](../getting-started/troubleshooting.md#native-hook-trust-and-reload)를 참고해 주세요. 설치 상태만으로 네이티브 실행을 입증하지 않습니다.

## State And Safety Model

설치기는 제거·복원할 대상을 추측하지 않도록 변경 내용을 기록합니다.

| State path | Why it exists |
| --- | --- |
| `~/.ghost-alice/install-state/<platform>.json` | 설치기가 관리한 대상과 환경 변경을 기록하며 상태 조회와 제거 명령이 읽습니다 |
| `~/.ghost-alice/pending-merges/<platform>/` | 설치기가 Ghost-ALICE 복사 설치 스킬 파일을 바꾸기 전에 사용자 수정본을 보존합니다. 설치 표식이 있는 폴더와, 이번 설치가 바꾸는 폴더는 표식이 사라졌어도 확인하며, merge-companion과 사용자가 병합 여부를 결정합니다. Claude 앱의 `synced`처럼 Ghost-ALICE가 설치하지 않는 폴더는 기록하지 않습니다 |
| `~/.ghost-alice/install/` | 감사와 복구에 필요한 설치 결과와 이벤트를 보관합니다 |

설치된 파일, 훅이나 실행 설정을 바꾸는 개발자는 상태 조회, 진단, 병합 대기 처리와 제거 복원에 미치는 영향도 확인해야 합니다. 이 절차들은 같은 설치 기록에 의존합니다.

## Contributor Entry Points

| Change type | Start here | Then verify with |
| --- | --- | --- |
| Python runtime detection or locale setup | `install.sh`, `install.ps1`, `install.cmd` | `scripts/tests/test_thin_shells.py`, `scripts/tests/test_install_runtime_detection.py`, compatibility matrix groups |
| Installer option, command dispatch or install order | `_shared/installer/cli.py`, `_shared/installer/auto.py` | `_shared/test_installer_cli.py`, `_shared/test_installer_auto.py`, `_shared/test_*_shell.py` end-to-end tests |
| Installer wording or the install report | `_shared/installer/report.py` | `_shared/test_installer_report.py`, `scripts/tests/test_installer_default_auto.py` |
| Hook payload or hook status wording | `_shared/install_hooks.py` | `_shared.test_install_hooks`, `scripts/check_skill_gate_contract.py` |
| Hook id, host event name or privileged adapter allowlist | `_shared/hook_definitions.py` | `_shared.test_hook_definitions` |
| Install-state or uninstall behavior | `_shared/install_state_writer.py`, `_shared/uninstall_cleanup.py`, `_shared/installer/lifecycle.py`, `_shared/installer/transaction.py` | `_shared/test_installer_lifecycle.py`, `_shared/test_installer_transaction.py`, install-state schema tests and uninstall tests |
| Public docs or command surface | `README.md`, `docs/`, `docs/index.html` | `scripts/validate_public_surfaces.py` and public-surface contract tests |
| Platform compatibility rule | `docs/policies/installer-platform-compatibility-matrix.md` | `scripts/run_installer_compat_tests.py --list` and policy가 지정한 focused group |

기여자는 구성 요소를 바꿀 때 연결된 계약을 확인합니다. 호환성 표가 지정한 담당 테스트에서 영향을 받는 회귀 검사를 찾습니다.

## What Not To Infer

- 훅 파일의 존재만으로 현재 입력·출력 계약과 일치한다고 판단하지 않습니다.
- 복사된 스킬 디렉터리만으로 플랫폼이 해당 스킬을 호출하거나 감사할 수 있다고 판단하지 않습니다.
- 설치 점검의 통과만으로 상태 조회·진단·업데이트·병합 대기·제거 절차의 일관성을 판단하지 않습니다.
- 사용자용 설치 명령은 진입점을 안내하며 내부 구조 설명을 대신하지 않습니다.

## Full Understanding

기여자는 검토할 변경이 셸 진입점에서 설치 조정 코드, 설치 대상, 상태 기록과 담당 테스트로 어떻게 이어지는지 확인합니다. 이 문서는 그 연결을 찾는 안내이며, 관련 구현과 호환성 계약을 읽는 절차를 대신하지 않습니다.
