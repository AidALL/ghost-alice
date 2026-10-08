# Official Addons

언어: [🇺🇸 English](../../reference/official-addons.md) | 🇰🇷 한국어

addon은 core skill 위에 선택적 capability package를 얹어 Ghost-ALICE OS install을 확장합니다. 이 문서는 homepage가 노출하는 addon source에 대한 contributor와 operator용 reference입니다. official alias, custom repository, tenant package, local development addon을 다루며, 모두 같은 installer entrypoint로 들어옵니다.

전체 install command surface는 [installation](../getting-started/installation.md)을 봅니다. 직접 만들거나 가져온 addon을 packaging하는 방법은 [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko)를 봅니다.

## Contents

- [Official Addons](#official-addons-1)
- [Custom, Tenant, And Local Development Addons](#custom-tenant-and-local-development-addons)
- [Where To Go Next](#where-to-go-next)

## Official Addons

official addon은 Ghost-ALICE core와 함께 유지되며, core checkout에서 짧은 alias로 설치합니다. repository URL을 몰라도 되고, alias가 유지되는 source로 연결됩니다.

official addon은 alias를 installer에 넘겨 설치합니다.

```bash
bash install.sh --addon autopilot
```

| Addon | Purpose | Install | Details |
| --- | --- | --- | --- |
| autopilot | 명시적 승인 이후 privileged autonomous adapter로 검증된 work item을 이어서 진행합니다. | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |
| ai-secretary | 발신자 승인에 따라 외부 메시지를 보내는 로컬 데스크톱 비서이며, OpenClaw 실행 어댑터를 교체할 수 있습니다. macOS 전용입니다. | `bash install.sh --addon ai-secretary` | [AidALL/ghost-alice-ai-secretary](https://github.com/AidALL/ghost-alice-ai-secretary) |

권장 제품 조합은 Core `0.4.1`과 Autopilot `0.4.1`입니다. 공유 SQLite 런타임에 필요한 애드온의 기술적 최소 Core 버전은 `0.4.0`으로 유지됩니다. 지원 범위는 애드온 호환성 표에서, 검증 범위는 [현재 릴리즈 안내](../release/2026-10-01-release-notes.md)에서 확인해 주세요.

Secretary 별칭은 소스 릴리즈 `v0.1.0`을 선택합니다. 고정된 설치 경로를 통해 버전이 지정된 OpenClaw·Node 런타임, 네이티브 데스크톱 작업기, 스킬 복사본과 플랫폼별 일반 레지스트리 기록을 하나의 소유 자원 묶음으로 설치합니다. Gateway를 시작하거나 모델을 설치하지는 않습니다. 자동 플랫폼 감지는 Claude와 Codex를 한 번의 묶음 설치로 처리하며, 일반 스킬 복사기가 Secretary를 중복 설치하지 않습니다. 현재 네이티브 작업기에는 macOS와 Xcode 명령행 도구가 필요합니다. 다른 운영체제에서는 묶음이나 core 설치를 변경하기 전에 중단합니다.

로컬 의존성 캐시를 사용하려면 버전이 지정된 패키지와 lockfile이 있는 npm prefix를 `SECRETARY_OPENCLAW_SOURCE`로, 지정된 Node 버전의 로컬 배포판을 `SECRETARY_NODE_SOURCE`로 설정합니다. Core는 이 두 경로만 소유권을 확인하는 설치기에 전달하며, 버전과 무결성 검사는 그대로 수행합니다. 이 설정은 명령어나 추가 인자를 받지 않습니다.

공유 Secretary 애드온은 `bash install.sh --platform codex --uninstall --addon ai-secretary`로 제거합니다. Claude에 설치한 경우에는 `claude`를 지정합니다. Core가 자원을 삭제하기 전에 소유권을 확인하는 묶음 제거기에 위임합니다. 실행 환경을 공유하므로 처음 설치에서 선택한 모든 플랫폼의 Secretary가 함께 제거됩니다. 사용자가 변경한 자원과 다른 애드온은 보존하며, 소유권이나 변경 여부를 확인할 수 없으면 수동 검토를 위해 제거를 중단합니다. Secretary의 작업 상태는 보존합니다.

Core의 일반 `--repair`는 Secretary 묶음과 소유권 기록을 보존하고 `use-owned-installer`를 안내합니다. 누락된 Secretary 스킬만 복사하거나 한 플랫폼의 기록을 다시 쓰지 않습니다. 설치된 Secretary 설치기의 `status`로 확인한 다음, 복구가 필요한 경우 원래 manifest 인자를 사용해 소유권을 확인하는 제거·재설치를 진행합니다.

기존 독립 설치가 루트 레지스트리 `~/.ghost-alice/addons`를 사용하거나 설치할 플랫폼을 바꾸려면 명시적으로 이전해야 합니다. `install-manifest.json`에 기록된 원래 `--prefix`, `--registry-dir`, 반복된 `--skills-dir` 인자를 사용해 설치된 `~/.local/share/ai-secretary/managed/app/scripts/installer.py uninstall`을 실행한 다음 공식 별칭으로 설치합니다. Core는 기존 소유권 기록을 자동으로 인수하거나 덮어쓰지 않습니다. 독립 설치와 복구에 관한 상세 내용은 [Secretary 저장소](https://github.com/AidALL/ghost-alice-ai-secretary)에서 확인해 주세요.

## Custom, Tenant, And Local Development Addons

addon이 official alias가 아니면 `--addon-source`로 명시적 path 또는 URL에서 설치합니다. source는 local directory, fork, tenant 전용 package, 개발 중인 checkout이 될 수 있습니다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source`는 repository URL도 받습니다.

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- custom repository: 직접 유지하거나 fork한 addon은 `--addon-source`를 그 repository URL로 지정해 설치합니다. installer는 official alias 대신 그 source에서 addon manifest를 읽습니다.
- tenant package: 조직 또는 고객 전용 addon bundle은 `--addon-source`를 tenant package 위치로 지정해 배포하고, 각 tenant는 권한이 있는 addon만 설치합니다.
- local development addon: addon을 작성하는 동안 `--addon-source`를 disk의 작업 checkout(예: `/path/to/addon-repo`)으로 지정하면 먼저 publish하지 않고도 설치하고 다시 테스트합니다.

## Where To Go Next

- [Installation](../getting-started/installation.md): core install, official addon, custom addon source, status, doctor, update, platform selection.
- [Addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko): 직접 만들거나 가져온 addon의 manifest format과 packaging 안내.
