# 애드온

[🇺🇸 English](../../reference/addons.md) | [🇰🇷 한국어](./addons.md)

애드온은 Ghost-ALICE OS 설치본에 Core 스킬 외의 선택 기능 패키지를 더합니다. 이 문서는 인간 사용자가 저장소, 조직별 패키지, 로컬 개발 체크아웃 중에서 애드온 설치 원본을 선택하도록 안내합니다. 모든 소스는 같은 인스톨러 진입점을 사용합니다.

전체 설치 명령은 [설치 안내](../getting-started/installation.md)에서 확인합니다. 직접 개발하거나 가져온 애드온을 패키지로 구성하는 방법은 [애드온 작성 안내](https://github.com/AidALL/ghost-alice/wiki/애드온-작성)에서 확인합니다.

## 목차

- [애드온 설치](#애드온-설치)
- [다음 단계](#다음-단계)

## 애드온 설치

애드온은 명시적인 경로나 URL을 `--addon-source`에 전달하여 설치합니다. 소스는 로컬 디렉터리, fork, 조직별 패키지나 개발 중인 체크아웃일 수 있습니다.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source`에는 저장소 URL도 지정하실 수 있습니다.

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- 사용자 정의 저장소: 직접 유지하거나 fork한 애드온은 그 저장소의 URL을 `--addon-source`에 지정하여 설치합니다. 인스톨러는 해당 소스의 애드온 매니페스트를 조회합니다.
- 조직별 패키지: 인간 사용자는 해당 조직·고객용 패키지를 선택하고, 인스톨러를 실행한 주체(인간 사용자, 응답 모델 또는 스크립트)가 그 위치를 `--addon-source`에 전달합니다. 인스톨러는 그 소스의 애드온 매니페스트를 조회합니다. 소스 선택 자체가 사용 권한을 부여하지는 않습니다.
- 로컬 개발 애드온: 개발자는 체크아웃의 디스크 경로(예: `/path/to/addon-repo`)를 `--addon-source`에 지정하여 게시 전에 설치하고 다시 테스트할 수 있습니다.

## 다음 단계

- [설치 안내](../getting-started/installation.md): Core 설치, 애드온 소스, status, doctor, 업데이트, 플랫폼 선택.
- [애드온 작성 안내](https://github.com/AidALL/ghost-alice/wiki/애드온-작성): 직접 개발하거나 가져온 애드온의 매니페스트 형식과 패키지 구성 안내.
