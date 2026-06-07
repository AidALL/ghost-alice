# Troubleshooting

언어: [🇺🇸 English](../../getting-started/troubleshooting.md) | 🇰🇷 한국어

이 문서는 Ghost-ALICE를 설치하거나 업데이트하는 중에 최신 repository docs를 pull하지 못할 때 빠르게 복구하는 playbook이다. repo update가 막힌 사용자도 읽을 수 있도록 같은 내용을 GitHub Wiki `install-troubleshooting_ko` page에도 올린다.

- Wiki: https://github.com/AidALL/ghost-alice/wiki/install-troubleshooting_ko
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

## Git Update Recovery

### Target Symptoms

아래 중 하나가 보이면 문제는 installer가 아니다. local checkout이 동기화되지 못한 상태이므로 Git state부터 고친다.

- `Committer identity unknown`
- `CONFLICT (content)`
- `CONFLICT (add/add)`
- `Automatic merge failed; fix conflicts and then commit the result.`
- `error: Your local changes to the following files would be overwritten by merge`
- `error: your local changes to the following files would be overwritten by merge`
- `## main...origin/main [ahead N, behind M]`
- `git status --short`에 `UU` 또는 `AA`가 보임

이 상태에서는 `install.ps1`, `install.sh`, `install.cmd`를 반복 실행하지 않는다. Git state를 먼저 깨끗하게 만든 뒤 installer를 다시 실행한다.

macOS/Linux의 일반 source update에서는 raw `git pull`보다 안전한 installer 경로를 먼저 쓴다.

```bash
cd ~/ghost-alice
bash install.sh --update-source
```

이 command는 source의 tracked/untracked 변경을 `git stash`에 저장하고, checkout을 fast-forward한 뒤, 그 stash를 직접 확인하도록 남긴다.

raw `git pull`이 이미 막혀서 checkout이 `--update-source`를 받지 못하면 bootstrap one-command update를 쓴다.

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

bootstrap updater는 낡은 local installer가 아니라 새로 가져온 remote blob에서 실행된다. source의 tracked/untracked 변경을 `git stash`에 저장하고 `~/ghost-alice`를 fast-forward한 뒤 updated installer를 실행한다. checkout 위치가 기본값이 아니면 command 앞에 `GHOST_ALICE_SOURCE_DIR=/path/to/ghost-alice`를 설정한다.

### 1. Fix Identity Errors First

`Committer identity unknown`은 Git이 merge commit을 만들 author identity를 모른다는 뜻이다. 본인 account 값으로 한 번 설정한다.

```powershell
git config --global user.email "you@example.com"
git config --global user.name "your-name"
```

conflict가 이미 시작됐으면 identity를 설정한 직후 `git pull`을 반복하지 않고, step 3 conflict recovery로 넘어간다.

### 2. Inspect Current State

```powershell
git status --short --branch
git diff --name-only --diff-filter=U
```

`UU` 또는 `AA`가 보이면 checkout은 이미 merge conflict 상태다. local changes를 보존할지 버릴지 먼저 정한다.

### 3. When Local Changes Are Not Needed

deployment clone, installer-only clone, 개인 edit이 없는 checkout처럼 local changes를 버려도 되는 경우에만 이 경로를 쓴다.

`git status --short`에 이미 `UU` 또는 `AA`가 있으면 먼저 merge를 abort한다.

```powershell
git merge --abort
```

그 다음 upstream을 다시 fetch하고 local checkout을 public `main`에 맞춘다.

```powershell
git fetch origin
git reset --hard origin/main
git clean -nd
git clean -fd
git pull --ff-only
```

Cautions:

- `git reset --hard origin/main`은 tracked 파일 내용과 local commit 위치를 `origin/main`으로 옮긴다.
- `git clean -nd`는 삭제 미리보기다.
- `git clean -fd`는 untracked 파일과 디렉터리를 실제로 삭제한다. 미리보기에 보존할 항목이 있으면 실행하지 않는다.

### 4. When Local Changes Must Be Preserved

local docs, skills, scripts를 편집했다면 destructive reset을 먼저 실행하지 않는다. 안전한 source update 경로를 쓰거나 backup branch와 diff를 만든다.

일반 fast-forward update의 권장 경로:

```bash
cd ~/ghost-alice
bash install.sh --update-source
git stash list
git stash show -p stash@{0}
```

checkout이 새 installer를 pull하지 못해 그 option이 아직 없으면 one-command bootstrap updater를 쓴다.

```bash
cd ~/ghost-alice && git fetch origin main && git show FETCH_HEAD:scripts/bootstrap-source-update.sh | /bin/bash -s --
```

source의 변경이 여전히 필요할 때만 stash를 다시 적용한다.

```bash
git stash pop stash@{0}
```

manual backup path:

```powershell
git status --short --branch
git branch backup/before-update-YYYYMMDD-HHMM
git diff > ghost-alice-local.diff
git diff --staged > ghost-alice-local-staged.diff
```

그 다음 `git status --short --branch`, conflict 파일 목록, 두 diff 파일을 maintainer에게 공유한다. 직접 해결하려면 conflict marker를 지우고 tests를 실행한 뒤 commit한다.

### 5. Run The Installer After Git Is Clean

Windows PowerShell:

```powershell
.\install.cmd
.\install.cmd -Doctor
.\install.cmd -Status
```

Windows PowerShell이 `Microsoft.PowerShell_profile.ps1` 또는 `.\install.ps1`에 대해 `cannot be loaded because running scripts is disabled`를 출력하면 `.\install.cmd`를 사용한다. wrapper는 이번 installer 실행에서 PowerShell을 `-NoProfile -ExecutionPolicy Bypass`로 시작하고, 사용자 또는 머신 execution policy를 변경하지 않는다.

macOS / Linux / WSL / Git Bash:

```bash
bash install.sh
bash install.sh --doctor
bash install.sh --status
```

`Doctor` 또는 `Status`가 pending merge warning을 내면, installer가 설치 중에 보호용 backup을 만들었다는 뜻이다. 새 Claude/Codex session을 열고 backup된 changes를 review해 달라고 요청하면, merge-companion flow가 무엇을 유지할지 정한다.

### 6. Operating Principles

- update command는 기본적으로 `git pull --ff-only`를 쓴다.
- `git pull`이 merge conflict를 내면 installer가 그 conflict를 대신 풀어 주지 못한다.
- 막힌 사용자를 위한 guide는 repo 안에만 두면 부족하다. Wiki나 release notes처럼 pull 없이 읽을 수 있는 위치에도 같은 recovery playbook을 둔다.
- Git status에 `UU` 또는 `AA`가 남아 있으면 installer를 실행하지 않는다.
