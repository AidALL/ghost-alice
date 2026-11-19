# 팀 작업 안내

이 폴더(`project-docs/`)는 팀(kyu, jy, sk)의 기획서, 보고서, 가이드를 모아 둔 곳입니다. 저장소 맨 위의 `README.md` 는 AidALL 원본 Core 문서라서, 팀 규칙은 이 파일에 적습니다.

## 1. 핵심 규칙: 환경은 태그, 문서는 main

- **설치할 때 Core는 반드시 태그 `test-baseline-2026-11-16` 으로 받습니다.** 이 태그가 팀 전체가 같은 코드로 테스트하는 기준 버전입니다.
- `main` 은 AidALL 최신 Core와 팀 문서(`project-docs/`)를 담는 브랜치입니다. 기준 버전 Core가 들어 있지 않으므로 `main` 으로 설치하지 않습니다.
- 기준 버전을 바꿔야 하면 팀원과 합의한 뒤 새 태그를 만듭니다. 기존 태그는 옮기지 않습니다.

## 2. 기준 버전

| 구성 요소 | 받을 곳 | 커밋 |
| --- | --- | --- |
| Core | 이 저장소의 태그 `test-baseline-2026-11-16` (브랜치 `local/v0.5.1-windows-fix` 와 같음) | `c6019ac` |
| Autopilot | 공식 `AidALL/ghost-alice-autopilot` 의 태그 `v0.5.1` | `2075768` |

설치 예시(PowerShell):

```powershell
cd $HOME
git clone --branch test-baseline-2026-11-16 https://github.com/bogop8-star/ghost-alice.git ghost-alice
git clone --branch v0.5.1 https://github.com/AidALL/ghost-alice-autopilot.git ghost-alice-autopilot
cd $HOME\ghost-alice
.\install.ps1 -Platform claude -AddonSource $HOME\ghost-alice-autopilot\addons\autopilot-mode
```

설치 뒤 `~/.ghost-alice/install-state/claude.json` 의 `source_head` 가 `c6019ac` 로 시작하는지 확인합니다.

## 3. 설치한 폴더에서 브랜치를 바꾸지 않기

이 버전의 훅은 설치할 때 쓴 Core 폴더(`~/ghost-alice`)의 코드를 직접 읽습니다. 설치 뒤 그 폴더에서 `git switch` 나 `git checkout` 으로 브랜치를 바꾸면, 설치 기록과 실제로 실행되는 코드가 달라집니다. 실제로 팀원 PC에서 이 때문에 Core 버전이 어긋난 적이 있습니다.

- 코드를 고치거나 다른 브랜치를 볼 때는 별도 폴더(`git worktree` 또는 새 clone)를 씁니다.
- 설치한 Autopilot 폴더의 어댑터 파일(`addons/`)도 바꾸지 않습니다. 설치 기록의 파일 지문과 달라집니다.

## 4. 브랜치 구성

| 브랜치·태그 | 내용 | PR 대상 |
| --- | --- | --- |
| `main` | AidALL 최신 Core + 팀 문서(`project-docs/`) | 문서 작업은 여기로 PR |
| 태그 `test-baseline-2026-11-16` | 설치 기준 Core (`c6019ac`) | 바꾸지 않음 |
| `autopilot/main` | Autopilot v0.5.1 + 팀 검증 변경(Windows 테스트 수정, 호환표 근거, CI 기준 고정) | Autopilot 작업은 여기로 PR |
| `autopilot/<작업이름>` | Autopilot 작업 브랜치. 예: `autopilot/windows-verify` | `autopilot/main` |
| `<이름>/<작업이름>` | 문서 등 개인 작업 브랜치. 예: `kyu/report-windows-verification` | `main` |

- Autopilot은 팀 fork가 따로 없어서 이 저장소의 `autopilot/` 브랜치에 둡니다. Autopilot PR은 `main` 이 아니라 `autopilot/main` 으로 올립니다.
- `autopilot/main` 의 변경은 테스트, 문서, 스크립트입니다. 어댑터 파일은 공식 `v0.5.1` 과 같으므로 설치는 위 2절처럼 공식 `v0.5.1` 로 합니다.
- `main` 과 `autopilot/main` 에는 바로 커밋하지 않습니다. 작업 브랜치에서 PR을 올리고, CI가 통과하고 팀원이 확인한 뒤 합칩니다.

## 5. 문서 목록

| 문서 | 내용 |
| --- | --- |
| [project-plan.md](project-plan.md) | 프로젝트 기획서 |
| [improvement-report.md](improvement-report.md) | 개선 보고서 |
| [bug-report-guide.md](bug-report-guide.md) | AidALL 버그 리포트 작성 가이드 |
| [reports/](reports/README.md) | 팀원별 진행 보고서와 공통 템플릿 |
