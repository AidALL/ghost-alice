# Official Addons

언어: [🇺🇸 English](../../reference/official-addons.md) | 🇰🇷 한국어

애드온은 Core 스킬에 선택적인 기능 패키지를 더합니다. 이 문서는 기여자와 운영자가 공식 별칭, 사용자 정의 저장소, 조직별 패키지와 로컬 개발 애드온의 설치 소스를 선택하도록 안내합니다. 모든 소스는 같은 Core 설치 진입점을 사용합니다.

전체 install command surface는 [installation](../getting-started/installation.md)을 봅니다. 직접 만들거나 가져온 addon을 packaging하는 방법은 [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko)를 봅니다.

## Contents

- [Official Addons](#official-addons-1)
- [Custom, Tenant, And Local Development Addons](#custom-tenant-and-local-development-addons)
- [Where To Go Next](#where-to-go-next)

## Official Addons

공식 애드온은 Ghost-ALICE Core와 함께 유지됩니다. 사용자는 Core 저장소에서 설치기에 짧은 별칭을 전달합니다. Core 설치기는 별칭에 해당하는 관리 소스를 찾아 해당 패키지의 애드온 매니페스트를 읽습니다.

공식 애드온을 설치하려면 설치기에 별칭을 전달해 주세요.

```bash
bash install.sh --addon autopilot
```

| Addon | Purpose | Install | Details |
| --- | --- | --- | --- |
| autopilot | 등록된 Stop 어댑터가 모델의 명시적 승인 계획에서 적격 작업 요청을 반환합니다. | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

Core `0.5.0`과 Ghost-ALICE Autopilot `0.5.0`은 routing과 managed completion API를 사용합니다. Ghost-ALICE Autopilot에는 Core `0.5.0` 이상이 필요합니다. 소스 검사, native runtime 검증과 게시는 서로 다른 근거를 제공합니다.

응답 모델은 작업과 의미를 판단하고 Core가 검토 기록을 인증합니다. Ghost-ALICE Autopilot은 인증 결과를 사용하며 모델이 등록한 승인 계획에서 적격 작업을 선택합니다. 입력·반환·호스트 전달은 [아키텍처](../../../architecture_ko.md)를 확인해 주세요.

## Custom, Tenant, And Local Development Addons

공식 별칭이 없는 애드온은 사용자가 명시적인 경로나 URL을 `--addon-source`에 전달하여 설치합니다. 소스는 로컬 디렉터리, fork, 조직별 패키지나 개발 중인 작업 사본일 수 있습니다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source`에는 저장소 URL도 지정하실 수 있습니다.

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- 사용자 정의 저장소: 직접 유지하거나 fork한 애드온의 URL을 `--addon-source`에 지정해 주세요. 설치기는 공식 별칭 대신 해당 소스의 애드온 매니페스트를 읽습니다.
- 조직별 패키지: 조직 운영자는 해당 조직·고객에게 승인된 패키지를 선택하여 그 위치를 `--addon-source`에 전달합니다. 설치기는 그 소스의 애드온 매니페스트를 읽습니다. 소스 선택 자체가 사용 권한을 부여하지는 않습니다.
- 로컬 개발 애드온: 개발자는 작업 사본의 디스크 경로(예: `/path/to/addon-repo`)를 `--addon-source`에 지정하여 게시 전에 설치하고 다시 테스트할 수 있습니다.

## Where To Go Next

- [Installation](../getting-started/installation.md): core install, official addon, custom addon source, status, doctor, update, platform selection.
- [Addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring_ko): 직접 만들거나 가져온 addon의 manifest format과 packaging 안내.
