# Troubleshooting

Language: [🇺🇸 English](../../getting-started/troubleshooting.md) | 🇰🇷 한국어

이 문서는 사용자가 Ghost-ALICE 설치·업데이트 중 저장소의 최신 문서를 가져오지 못할 때 따르는 복구 절차입니다. GitHub Wiki의 홈 페이지가 이 온라인 안내로 연결하므로 로컬 저장소를 갱신하지 않고 읽으실 수 있습니다.

- Wiki: [GitHub 위키 홈](https://github.com/AidALL/ghost-alice/wiki/홈)
- Repo copy: `docs/ko/getting-started/troubleshooting.md`

## Contents

- [Git Update Recovery](#git-update-recovery)
- [Target Symptoms](#target-symptoms)
- [1. Fix Identity Errors First](#1-fix-identity-errors-first)
- [2. Inspect Current State](#2-inspect-current-state)
- [3. When Local Changes Are Not Needed](#3-when-local-changes-are-not-needed)
- [4. When Local Changes Must Be Preserved](#4-when-local-changes-must-be-preserved)
- [5. Run The Installer After Git Is Clean](#5-run-the-installer-after-git-is-clean)
- [6. Operating Principles](#6-operating-principles)
- [Privileged Adapter Registration Mismatch](#privileged-adapter-registration-mismatch)
- [Native Hook Trust And Reload](#native-hook-trust-and-reload)

## Git Update Recovery

### Target Symptoms

아래 중 하나가 보이면 문제는 installer가 아닙니다. local checkout이 동기화되지 못한 상태이므로 Git state부터 고칩니다.

- `Committer identity unknown`
- `CONFLICT (content)`
- `CONFLICT (add/add)`
- `Automatic merge failed; fix conflicts and then commit the result.`
- `error: Your local changes to the following files would be overwritten by merge`
- `error: your local changes to the following files would be overwritten by merge`
- `## main...origin/main [ahead N, behind M]`
- `git status --short`에 `UU` 또는 `AA`가 보임

이 상태에서는 installer를 반복 실행하지 않습니다. Git state를 먼저 깨끗하게 만든 뒤 installer를 다시 실행합니다.

일반 source update에서는 raw `git pull`보다 안전한 installer 경로를 먼저 씁니다.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

설치기는 이 명령에서 소스의 추적·미추적 변경을 `git stash`에 저장하고 작업 사본을 fast-forward합니다. 저장한 stash는 사용자가 직접 확인할 수 있도록 남겨 둡니다.

Windows에서는 같은 installer path에 `.\install.cmd`를 사용합니다. PowerShell이 `cannot be loaded because running scripts is disabled`를 표시하면 wrapper가 `-NoProfile -ExecutionPolicy Bypass`로 PowerShell을 호출하고 사용자 또는 머신 execution policy를 변경하지 않습니다.

raw `git pull`이 이미 막혀서 checkout이 `--update-source`를 받지 못하면 bootstrap one-command update를 씁니다.

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

bootstrap updater는 local installer와 독립적으로 가져온 remote blob에서 실행됩니다. source의 tracked/untracked 변경을 `git stash`에 저장하고 `~/ghost-alice`를 fast-forward한 뒤 updated installer를 실행합니다. checkout 위치가 기본값이 아니면 command 앞에 `GHOST_ALICE_SOURCE_DIR=/path/to/ghost-alice`를 설정합니다.

### 1. Fix Identity Errors First

`Committer identity unknown`은 Git이 merge commit을 만들 author identity를 모른다는 뜻입니다. 본인 account 값으로 한 번 설정합니다.

```bash
git config --global user.email "you@example.com"
git config --global user.name "your-name"
```

병합 충돌이 이미 시작됐다면 신원을 설정한 직후 `git pull`을 반복하지 마세요. 먼저 2단계에서 상태를 확인하고, 로컬 변경을 버려도 되는지에 따라 3단계 또는 4단계를 선택해 주세요.

### 2. Inspect Current State

```bash
git status --short --branch
git diff --name-only --diff-filter=U
```

`UU` 또는 `AA`가 보이면 checkout은 이미 merge conflict 상태입니다. local changes를 보존할지 버릴지 먼저 정합니다.

### 3. When Local Changes Are Not Needed

deployment clone, installer-only clone, 개인 edit이 없는 checkout처럼 local changes를 버려도 되는 경우에만 이 경로를 씁니다.

`git status --short`에 이미 `UU` 또는 `AA`가 있으면 먼저 merge를 abort합니다.

```bash
git merge --abort
```

그 다음 upstream을 다시 fetch하고 local checkout을 public `main`에 맞춥니다.

```bash
git fetch origin
git reset --hard origin/main
git clean -nd
git clean -fd
git pull --ff-only
```

Cautions:

- `git reset --hard origin/main`은 tracked 파일 내용과 local commit 위치를 `origin/main`으로 옮깁니다.
- `git clean -nd`는 삭제 미리보기입니다.
- `git clean -fd`는 untracked 파일과 디렉터리를 실제로 삭제합니다. 미리보기에 보존할 항목이 있으면 실행하지 않습니다.

### 4. When Local Changes Must Be Preserved

local docs, skills, scripts를 편집했다면 destructive reset을 먼저 실행하지 않습니다. 안전한 source update 경로를 쓰거나 backup branch와 diff를 만듭니다.

일반 fast-forward update의 권장 경로:

```bash
cd ~/ghost-alice
bash install.sh --update-source
git stash list
git stash show -p stash@{0}
```

로컬 설치기에 업데이트 옵션이 없으면 위 [Git Update Recovery](#git-update-recovery)의 부트스트랩 경로를 사용해 주세요.

source의 변경이 여전히 필요할 때만 stash를 다시 적용합니다.

```bash
git stash pop stash@{0}
```

manual backup path:

```bash
git status --short --branch
git branch backup/before-update-YYYYMMDD-HHMM
git diff > ghost-alice-local.diff
git diff --staged > ghost-alice-local-staged.diff
```

그 다음 `git status --short --branch`, 충돌 파일 목록과 두 diff 파일을 관리자에게 공유합니다. 직접 해결하시려면 충돌 표시를 정리하고 테스트를 실행한 뒤 commit해 주세요.

### 5. Run The Installer After Git Is Clean

소스를 복구하신 뒤 Core와 Ghost-ALICE Autopilot을 함께 다시 설치해 주세요. `--update-source`만으로는 Core 소스만 갱신되며, 아래 함께 설치하는 명령이 애드온을 가져옵니다. Windows에서는 `.\install.cmd --addon autopilot`을 실행한 뒤 `--doctor`와 `--status`로 확인해 주세요.

```bash
bash install.sh --addon autopilot
bash install.sh --doctor
bash install.sh --status
```

`Doctor` 또는 `Status`가 pending merge warning을 내면, installer가 설치 중에 보호용 backup을 만들었다는 뜻입니다. 새 Claude/Codex 세션에서 백업된 변경의 검토를 요청해 주세요. merge-companion은 선택지를 설명하고 사용자가 무엇을 유지할지 결정합니다.

### 6. Operating Principles

- update command는 기본적으로 safe installer updater를 씁니다. updater 내부에서 source-local changes를 저장한 뒤 checkout을 fast-forward합니다.
- `git pull`이 merge conflict를 내면 installer가 그 conflict를 대신 풀어 주지 못합니다.
- 막힌 사용자를 위한 guide는 repo 안에만 두면 부족합니다. Wiki에서 온라인 소스 안내로 연결하여 pull 없이 읽을 수 있게 합니다.
- Git status에 `UU` 또는 `AA`가 남아 있으면 installer를 실행하지 않습니다.

## Privileged Adapter Registration Mismatch

Core 훅 실행기는 등록된 명령에 privileged adapter의 스크립트 해시·이벤트·출처를 고정합니다. 값이 맞지 않으면 어댑터 실행 전에 종료 코드 126으로 거부합니다. 거부 메시지는 `hash`, `event`, `source` 중 원인을 표시하며, 엄격한 훅 로그는 원래 네이티브 세션과 `surface_item.rejection_diagnostic`에 진단을 보존합니다.

해시 불일치라면 거부된 명령의 예상 해시, 설치된 스크립트의 실제 해시와 현재 등록된 명령을 비교해 주세요. 현재 등록과 설치된 파일이 일치하지만 거부된 호출이 이전 해시를 요구한다면 해당 호출은 이전 등록을 사용한 것입니다. 호스트가 제공하는 세션 재로드·재시작 절차로 읽은 훅 명령을 갱신해 주세요. 등록 파일을 바꾼 것만으로 실행 중인 호스트의 반영을 입증할 수 없습니다. 현재 등록도 설치된 파일과 다르다면 로컬 변경을 보존한 뒤 관리 설치와 등록을 함께 복구해 주세요. 이벤트·출처 불일치는 Core 설치기를 통해 해당 등록 연결을 복구합니다.

등록 복구를 위해 해시 검증을 우회하거나 원래 근거의 시각을 새로 만들거나 변경 없는 업무 검증을 반복하지 마세요. 이후 실제 네이티브 Ghost-ALICE Autopilot 콜백으로 복구를 확인합니다. 접수된 `pending-adapter` 제출과 적용된 `applied` 결과·해당 세션 Stop 로그의 적용 참조는 구별합니다.

## Native Hook Trust And Reload

설치 상태, 네이티브 신뢰 상태와 실행 중인 호스트의 반영을 따로 확인해 주세요. 설치된 스크립트와 등록 명령이 일치해도 호스트가 이전 명령이나 신뢰 결정을 유지할 수 있습니다. 네이티브 훅의 `modified` 상태는 신뢰 불일치를 가리키며 Core 어댑터 실행기의 종료 코드 126 거부와는 다릅니다.

1. 로컬 설정을 보존한 뒤 Core 설치기로 관리되는 훅 명령과 네이티브 신뢰 항목을 함께 맞춥니다. 새 해시를 신뢰하기 전에 실제로 바뀐 명령을 검토합니다. 다른 신뢰 항목과 각 훅의 활성화·비활성화 선택은 유지합니다.
2. 호스트의 재로드 기능을 사용합니다. 실행 중인 호스트가 이전 명령이나 신뢰 항목을 계속 사용하면 앱을 완전히 종료한 뒤 다시 엽니다. 새 대화를 만드는 것만으로 재로드가 입증되지는 않습니다.
3. 해당 세션의 다음 실제 입력·라우팅·Ghost-ALICE Autopilot 콜백을 관측합니다. 설치 파일, 별도 진단 프로세스와 성공적으로 접수된 제출만으로 현재 호스트가 무엇을 호출했는지 판단하지 않습니다.

엄격한 감사는 유지합니다. 검사를 끄거나 완료된 업무 검증을 반복하지 않고 영향받은 등록·신뢰 연결을 복구합니다. 기록 실패가 남으면 이미 근거가 있는 업무 결과와 구별하여 보고합니다.
