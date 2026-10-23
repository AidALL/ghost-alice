# Addons

Language: [🇺🇸 English](../../reference/official-addons.md) | 🇰🇷 한국어

애드온은 Core 스킬에 선택적인 기능 패키지를 더합니다. 이 문서는 기여자와 운영자가 저장소, 조직별 패키지, 로컬 개발 사본 중에서 애드온 설치 소스를 선택하도록 안내합니다. 모든 소스는 같은 Core 설치 진입점을 사용합니다.

전체 install command surface는 [installation](../getting-started/installation.md)을 봅니다. 직접 만들거나 가져온 addon을 packaging하는 방법은 [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/애드온-작성)를 봅니다.

## Contents

- [Install An Addon](#install-an-addon)
- [Where To Go Next](#where-to-go-next)

## Install An Addon

애드온은 명시적인 경로나 URL을 `--addon-source`에 전달하여 설치합니다. 소스는 로컬 디렉터리, fork, 조직별 패키지나 개발 중인 작업 사본일 수 있습니다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source`에는 저장소 URL도 지정하실 수 있습니다.

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- 사용자 정의 저장소: 직접 유지하거나 fork한 애드온의 URL을 `--addon-source`에 지정해 주세요. 인스톨러는 해당 소스의 애드온 매니페스트를 읽습니다.
- 조직별 패키지: 조직 운영자는 해당 조직·고객에게 승인된 패키지를 선택하여 그 위치를 `--addon-source`에 전달합니다. 인스톨러는 그 소스의 애드온 매니페스트를 읽습니다. 소스 선택 자체가 사용 권한을 부여하지는 않습니다.
- 로컬 개발 애드온: 개발자는 작업 사본의 디스크 경로(예: `/path/to/addon-repo`)를 `--addon-source`에 지정하여 게시 전에 설치하고 다시 테스트할 수 있습니다.

## Where To Go Next

- [Installation](../getting-started/installation.md): core install, addon source, status, doctor, update, platform selection.
- [Addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/애드온-작성): 직접 만들거나 가져온 addon의 manifest format과 packaging 안내.
