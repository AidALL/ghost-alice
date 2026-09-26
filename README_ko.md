# Ghost-ALICE OS

언어: [English](./README.md) | Korean

![Ghost-ALICE OS logo](imgs/Ghost-ALICE_logo.png)

Ghost-ALICE OS는 AI 에이전트의 작업을 관리하는 거버넌스 계층입니다. 지원하는 에이전트 실행 환경에서 사용자의 의도, 작업 범위, 검증 근거, 실행 상태를 확인할 수 있도록 관리합니다.

기존 에이전트 실행 환경 위에서 동작하며, 에이전트가 완료를 선언하기 전에 작업 과정과 근거를 검토할 수 있도록 돕습니다. 프롬프트 모음이나 챗봇 연결 도구, 독립 실행형 에이전트 런타임을 제공하는 저장소는 아닙니다.

## Quick Start

macOS, Linux, WSL, Git Bash:

```bash
git clone https://github.com/AidALL/ghost-alice.git ~/ghost-alice
cd ~/ghost-alice
bash install.sh
```

Windows Command Prompt 또는 PowerShell:

```cmd
git clone https://github.com/AidALL/ghost-alice.git %USERPROFILE%\ghost-alice
cd %USERPROFILE%\ghost-alice
.\install.cmd
```

사용하시는 운영체제에 맞는 설치 명령을 실행해 주세요. Windows에서는 `.\install.cmd ...`를, macOS, Linux, WSL, Git Bash에서는 `bash install.sh ...`를 사용하며, 두 명령은 같은 긴 형식의 옵션을 지원합니다. `install.cmd`는 Windows용 래퍼를 통해 실행되고 Python 3.11 이상을 요구하며, 콘솔을 UTF-8로 설정합니다. PowerShell 호출에는 `-NoProfile -ExecutionPolicy Bypass`를 사용하여 실행 정책으로 인한 차단을 처리하지만, 사용자나 시스템에 저장된 실행 정책은 변경하지 않습니다.

## Official Addons

공식 애드온은 필요한 기능을 추가하는 확장 패키지입니다. 별도 저장소에서 관리하며, 위에서 내려받은 Ghost-ALICE 폴더에서 짧은 이름으로 설치하실 수 있습니다.

macOS, Linux, WSL, Git Bash:

```bash
bash install.sh --addon <addon>
```

Windows Command Prompt 또는 PowerShell:

```cmd
.\install.cmd --addon <addon>
```

일부 공식 애드온은 특정 작업을 위한 스킬만 추가하고, 다른 애드온은 실행 절차까지 확장합니다. 애드온별 동작 방식, 상태 파일, 일시 정지·재개 방법, 제거 절차는 해당 애드온 저장소에서 확인해 주세요.

Autopilot은 Ghost-ALICE 설치기로 설치하는 애드온 패키지입니다. 아래 설치 예시가 모든 실행 환경과의 호환성을 보장하지는 않습니다. 사용하실 환경의 지원 범위는 애드온 저장소의 `compatibility-matrix.json`에서 확인해 주세요.

| 애드온 | 용도 | 기본 설치 명령 | 자세한 안내 |
| --- | --- | --- | --- |
| autopilot | 명시적으로 승인하신 자율 실행을 작업 항목 단위로 이어갑니다. | `bash install.sh --addon autopilot` / `.\install.cmd --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

사용자 정의 애드온, 조직별 애드온, 로컬에서 개발 중인 애드온은 `--addon-source`로 설치하실 수 있습니다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

```cmd
.\install.cmd --addon-source C:\path\to\addon-repo
```

## Documentation Map

| 필요한 정보 | 안내 문서 |
| --- | --- |
| 전체 설치·업데이트 명령 | [Installation and update guide](./docs/ko/getting-started/installation.md) |
| 제거 범위와 정리 방식 | [Uninstall cleanup procedure](./docs/ko/getting-started/uninstall.md) |
| 업데이트 실패, 병합 충돌, 재설치 복구 | [Troubleshooting](./docs/ko/getting-started/troubleshooting.md) |
| 저장소 구조 | [Repository structure](./docs/ko/reference/repository-structure.md) |
| 스킬 목록과 참조 정보 | [Skill catalog guide](./docs/ko/reference/skills.md) |
| 세션 처리 단계와 필수 검사 규칙 | [Session gate matrix](./docs/ko/policies/session-gate-matrix.md) |
| 설치기의 플랫폼별 호환성 기준 | [Installer platform compatibility](./docs/ko/policies/installer-platform-compatibility-matrix.md) |
| 팀 도입 안내와 설계 배경 | [GitHub Wiki](https://github.com/AidALL/ghost-alice/wiki) |
| 공식 애드온 사용법 | [Wiki: official addons](https://github.com/AidALL/ghost-alice/wiki/official-addons_ko) |
| 애드온 제작 방법 | [Wiki: addon authoring](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko) |

## Project Guarantees

- Ghost-ALICE OS는 에이전트 세션의 작업을 관리하는 운영 계층이며, 범용 운영체제는 아닙니다.
- 사용자의 의도, 작업 범위, 검증 기준, 설치 상태를 핵심 관리 대상으로 다룹니다.
- 세션 라우팅은 도구를 실행하기 전에 기록된 의도와 후속 검사 단계의 상태를 확인합니다.
- 실행, 수정, 검증이 끝났다고 선언하려면 현재 상태를 뒷받침하는 근거가 필요합니다.
- 설치기는 자신이 관리하는 파일과 설정을 추적하며, 관리 대상임을 입증할 수 없으면 삭제 범위를 넓히지 않습니다.

작업 관리 메시지의 표시 수준은 다음 명령으로 조절하실 수 있습니다.

- Claude Code에서는 작업 공간 명령인 `/visibility strict|dynamic|minimal`을 사용합니다.
- Codex에서는 신뢰된 `UserPromptSubmit` 훅의 의사 명령 경로를 통해 `/visibility`를 처리합니다.
- 모든 플랫폼에서 `_shared/agent_visibility_cli.py`를 통해 같은 프로필 값을 확인하고 변경하실 수 있습니다.
- 설치 시 기본값은 `dynamic`입니다. 초기 프로필은 `bash install.sh --visibility dynamic` 또는 `.\install.cmd --visibility dynamic`으로 설정하실 수 있습니다. `--agent-visibility`도 호환성을 위한 별칭으로 유지됩니다.
- 표시 수준은 사용자에게 보이는 작업 관리 메시지만 바꿉니다. 훅 실행, 엄격한 감사 로그, 작업 영향 분류인 Work-Impact Projection은 축소하지 않습니다.

전체 제거가 필요하시면 다음 명령을 사용해 주세요.

```bash
bash install.sh --uninstall
```

```cmd
.\install.cmd --uninstall
```

## Contributing And Validation

스킬, 설치기 동작, 공개 명령을 변경하기 전에는 [AGENTS.md](./AGENTS.md)를 읽어 주세요. 새 스킬을 만들거나 기존 스킬을 수정하신 뒤에는 [official-docs/derived/skill-compliance-checklist.md](./official-docs/derived/skill-compliance-checklist.md)의 Phase 1-5를 통과해야 합니다.

공개 문서와 명령 안내의 일관성은 다음 명령으로 검증하실 수 있습니다.

```bash
python3 scripts/validate_public_surfaces.py
```

세션 검사 규칙 검증 명령은 저장소의 `skill-catalog/session-gates.json`과 사용자용 정책 안내를 함께 확인합니다.

```bash
python scripts/check_skill_gate_contract.py
```

설치기 호환성 검사 그룹은 다음 명령으로 조회하고 실행하실 수 있습니다.

```bash
python3 scripts/run_installer_compat_tests.py --list
python3 scripts/run_installer_compat_tests.py --group public-surface-contract
```

## License

Ghost-ALICE OS 프로젝트가 소유한 소스 코드와 문서는 Apache License, Version 2.0으로 제공됩니다. 자세한 내용은 [LICENSE](./LICENSE), [NOTICE](./NOTICE), [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)를 확인해 주세요.
