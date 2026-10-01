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

권장 제품 조합은 Core `0.4.1`과 Autopilot `0.4.1`입니다. 공유 SQLite 런타임에 필요한 애드온의 기술적 최소 Core 버전은 `0.4.0`으로 유지됩니다. 지원 범위는 애드온 호환성 표에서, 검증 범위는 [현재 릴리즈 안내](../release/2026-10-01-release-notes.md)에서 확인해 주세요.

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
