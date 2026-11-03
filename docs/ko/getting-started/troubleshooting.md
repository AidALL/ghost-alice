# 문제 해결

[🇺🇸 English](../../getting-started/troubleshooting.md) | [🇰🇷 한국어](./troubleshooting.md)

이 문서는 사용자가 Ghost-ALICE 설치·업데이트 중 저장소의 최신 문서를 가져오지 못할 때 따르는 복구 절차입니다. GitHub 위키의 홈 페이지가 이 온라인 안내로 연결하므로 로컬 저장소를 갱신하지 않고 조회하실 수 있습니다.

- 위키: [GitHub 위키 홈](https://github.com/AidALL/ghost-alice/wiki/홈)
- 저장소 사본: `docs/ko/getting-started/troubleshooting.md`

## 목차

- [Git 업데이트 복구](#git-업데이트-복구)
- [대상 증상](#대상-증상)
- [1. 신원 오류를 먼저 수정](#1-신원-오류를-먼저-수정)
- [2. 현재 상태 조회](#2-현재-상태-조회)
- [3. 로컬 변경을 버려도 될 때](#3-로컬-변경을-버려도-될-때)
- [4. 로컬 변경을 보존해야 할 때](#4-로컬-변경을-보존해야-할-때)
- [5. Git이 깨끗해진 뒤 인스톨러 실행](#5-git이-깨끗해진-뒤-인스톨러-실행)
- [6. 운영 원칙](#6-운영-원칙)
- [네이티브 훅 신뢰와 재로드](#네이티브-훅-신뢰와-재로드)
- [입력 경고와 doctor 목록](#입력-경고와-doctor-목록)

## Git 업데이트 복구

### 대상 증상

아래 중 하나가 보이면 먼저 해결할 문제는 인스톨러가 아닙니다. 로컬 체크아웃이 동기화되지 못한 상태이므로 Git 상태부터 복구합니다.

- `Committer identity unknown`
- `CONFLICT (content)`
- `CONFLICT (add/add)`
- `Automatic merge failed; fix conflicts and then commit the result.`
- `error: Your local changes to the following files would be overwritten by merge`
- `error: 다음 파일의 로컬 변경 사항을 병합 때문에 덮어 쓰게 됩니다` (한국어 로케일 Git이 표시하는 같은 오류)
- `## main...origin/main [ahead N, behind M]`
- `git status --short`에 `UU` 또는 `AA`가 보임

이 상태에서는 인스톨러를 반복 실행하지 않습니다. Git 상태를 먼저 깨끗하게 복구한 뒤 인스톨러를 다시 실행합니다.

일반 소스 업데이트에는 직접 실행하는 `git pull`보다 안전한 인스톨러 업데이트 명령을 우선 사용합니다.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

인스톨러는 이 명령에서 소스의 추적·미추적 변경을 `git stash`에 저장하고 체크아웃을 fast-forward합니다. 저장한 stash는 사용자가 직접 확인할 수 있도록 보관합니다.

Windows에서는 같은 업데이트에 `.\install.cmd`를 실행합니다. PowerShell이 `cannot be loaded because running scripts is disabled`를 표시하면 래퍼가 `-NoProfile -ExecutionPolicy Bypass`로 PowerShell을 호출하고 사용자 또는 컴퓨터의 실행 정책을 변경하지 않습니다.

직접 실행한 `git pull`이 이미 막혀서 체크아웃이 `--update-source`를 받지 못하면 아래의 부트스트랩 단일 명령 업데이트를 실행합니다.

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

부트스트랩 갱신기는 가져온 원격 blob에서 로컬 인스톨러와 독립적으로 실행됩니다. 소스의 추적·미추적 변경을 `git stash`에 저장하고 `~/ghost-alice`를 fast-forward한 뒤 갱신된 인스톨러를 실행합니다. 체크아웃 위치가 기본값이 아니면 명령 앞에 `GHOST_ALICE_SOURCE_DIR=/path/to/ghost-alice`를 설정합니다.

### 1. 신원 오류를 먼저 수정

`Committer identity unknown`은 작성자 신원이 없어 Git이 병합 커밋을 생성하지 못한다는 뜻입니다. 본인 계정 값으로 한 번 설정합니다.

```bash
git config --global user.email "you@example.com"
git config --global user.name "your-name"
```

병합 충돌이 이미 시작됐다면 신원을 설정한 직후 `git pull`을 반복하지 마세요. 먼저 2단계에서 상태를 확인하고, 로컬 변경을 버려도 되는지에 따라 3단계 또는 4단계를 선택해 주세요.

### 2. 현재 상태 조회

```bash
git status --short --branch
git diff --name-only --diff-filter=U
```

`UU` 또는 `AA`가 보이면 체크아웃은 이미 병합 충돌 상태입니다. 로컬 변경을 보존할지 버릴지 먼저 결정합니다.

### 3. 로컬 변경을 버려도 될 때

배포용 복제본, 인스톨러 전용 복제본, 개인 수정이 없는 체크아웃처럼 로컬 변경을 버려도 되는 경우에만 이 절차를 사용합니다.

`git status --short`에 이미 `UU` 또는 `AA`가 있으면 먼저 병합을 중단합니다.

```bash
git merge --abort
```

그다음 `origin`을 다시 fetch하고 로컬 체크아웃을 공개 `main`과 일치시킵니다.

```bash
git fetch origin
git reset --hard origin/main
git clean -nd
git clean -fd
git pull --ff-only
```

주의 사항:

- `git reset --hard origin/main`은 현재 브랜치를 `origin/main`으로 이동하고 추적 파일의 내용을 그 커밋의 내용으로 교체합니다.
- `git clean -nd`는 삭제 미리보기입니다.
- `git clean -fd`는 미추적 파일과 디렉터리를 실제로 삭제합니다. 미리보기에 보존할 항목이 있으면 실행하지 않습니다.

### 4. 로컬 변경을 보존해야 할 때

로컬 문서·스킬·스크립트를 편집했다면 파괴적인 reset을 먼저 실행하지 않습니다. 안전한 소스 업데이트 명령을 사용하거나 백업 브랜치를 생성하고 diff를 저장합니다.

일반 fast-forward 업데이트의 권장 절차:

```bash
cd ~/ghost-alice
bash install.sh --update-source
git stash list
git stash show -p stash@{0}
```

로컬 인스톨러에 업데이트 옵션이 없으면 위 [Git 업데이트 복구](#git-업데이트-복구)의 부트스트랩 업데이트를 사용해 주세요.

stash에 저장한 로컬 변경이 여전히 의도한 변경일 때만 stash를 다시 적용합니다.

```bash
git stash pop stash@{0}
```

수동 백업 절차:

```bash
git status --short --branch
git branch backup/before-update-YYYYMMDD-HHMM
git diff > ghost-alice-local.diff
git diff --staged > ghost-alice-local-staged.diff
```

그다음 `git status --short --branch`, 충돌 파일 목록과 두 diff 파일을 관리자에게 공유합니다. 직접 해결하시려면 충돌 표시를 삭제하고 테스트를 실행한 뒤 결과를 커밋해 주세요.

### 5. Git이 깨끗해진 뒤 인스톨러 실행

소스를 복구하신 뒤 Core를 다시 설치해 주세요. `--update-source`만으로는 Core 소스만 갱신되며, 아래 설치 명령이 설치를 수행합니다. 설치된 애드온은 그대로 유지됩니다. Windows에서는 `.\install.cmd`를 실행한 뒤 `--doctor`와 `--status`로 확인해 주세요.

```bash
bash install.sh
bash install.sh --doctor
bash install.sh --status
```

`Doctor` 또는 `Status`가 병합 대기(`pending-merge`) 경고를 표시하면, 인스톨러가 설치 중에 보호용 백업을 생성했다는 뜻입니다. 새 Claude/Codex 세션에서 백업된 변경의 검토를 요청해 주세요. merge-companion 절차가 선택지를 설명하면 사용자가 무엇을 유지할지 결정합니다.

### 6. 운영 원칙

- 업데이트 명령은 기본적으로 안전한 인스톨러 갱신기를 사용합니다. 갱신기가 소스의 로컬 변경을 저장한 뒤 체크아웃을 직접 fast-forward합니다.
- `git pull`이 병합 충돌을 일으키면 인스톨러가 사용자 대신 그 충돌을 해결하지 못합니다.
- 막힌 사용자를 위한 안내가 저장소 안에만 있으면 부족합니다. 관리자는 위키에 이 안내의 온라인 링크를 유지해, 사용자가 pull 없이 조회할 수 있게 합니다.
- Git 상태에 `UU` 또는 `AA`가 남아 있으면 인스톨러를 실행하지 않습니다.

## 네이티브 훅 신뢰와 재로드

설치 상태, 네이티브 신뢰 상태와 실행 중인 호스트의 반영을 따로 확인해 주세요. 설치된 스크립트와 등록 명령이 일치해도 호스트가 이전 명령이나 신뢰 결정을 유지할 수 있습니다. 네이티브 훅의 `modified` 상태는 신뢰 불일치를 가리킵니다.

1. 로컬 설정을 보존한 뒤 Core 인스톨러로 관리되는 훅 명령과 네이티브 신뢰 항목을 함께 맞춥니다. 새 해시를 신뢰하기 전에 실제로 바뀐 명령을 검토합니다. 다른 신뢰 항목과 각 훅의 활성화·비활성화 선택은 유지합니다.
2. 호스트의 재로드 기능을 사용합니다. 실행 중인 호스트가 이전 명령이나 신뢰 항목을 계속 사용하면 앱을 완전히 종료한 뒤 다시 엽니다. 새 대화를 만드는 것만으로 재로드가 입증되지는 않습니다.
3. 해당 세션의 다음 실제 입력과 라우팅을 관측합니다. 설치 파일과 별도 진단 프로세스만으로 현재 호스트가 무엇을 호출했는지 판단하지 않습니다.

Ghost-ALICE 훅과 감사 기록을 끄지 말고 영향받은 등록과 신뢰 항목을 복구해 주세요. 완료된 업무 검증은 다시 실행하지 말고 [근거 재사용 규칙](../../../architecture_ko.md#근거와-네이티브-표시)대로 재사용해 주세요.

## 입력 경고와 doctor 목록

Claude Code에 `Ghost-ALICE intake: records that need a confirmed input (intent, route and completion) are paused in this session because ...`가 표시될 수 있습니다. Core가 이 세션의 최근 입력 출처를 확인하지 못했다는 뜻입니다. 예를 들어 다른 세션이 보낸 메시지이거나 대화 기록에서 행을 찾지 못한 입력이며, 세션은 사용자가 다음에 직접 입력하는 내용을 기다립니다. 작업은 계속되지만, 그때까지 Core는 의도·응답 경로·완료를 기록하지 않습니다. 경고는 같은 세션·같은 원인으로 한 번 표시되며, 같은 원인의 이후 대기는 기록만 됩니다. [표시 프로필](./installation.md#에이전트-표시-프로필)에 따라 훅의 빈 결과가 일반 텍스트 줄로 변경될 수 있습니다. 그 훅은 경고를 전달하지 못하므로, 경고는 다음 PreToolUse 또는 Stop에 표시됩니다.

1. 그 세션에서 평소처럼 다음 입력을 보내 주세요. Core가 그 입력을 확인하고 기록을 다시 시작합니다.
2. `bash install.sh --doctor`(Windows: `.\install.cmd --doctor`)를 실행해 주세요. `native-intake-waits`는 아직 기다리는 세션과 그 원인을 보이고, `native-intake-failures`는 Core의 입력 판정이 실패한 세션과 그 예외 종류·횟수·마지막 시각을 보입니다. 훅은 각 실패 뒤에도 계속되었습니다.
3. 직접 입력을 보낸 뒤에도 세션이 계속 기다리거나 `native-intake-failures`가 계속 늘면, 그 doctor 줄을 관리자에게 전달해 주세요. 원장은 직접 수정하지 마세요.
