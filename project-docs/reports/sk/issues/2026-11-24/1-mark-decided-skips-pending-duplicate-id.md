재현 여부: sk 한 명이 한 PC에서 실제 manifest 1회와 최소 재현 스크립트로 재현. 기준 commit `c6019ac`에서만 재현되고 최신 `main`(`e4df73c`)에서는 재현되지 않음.

제목: [bug]: merge-companion mark_decided updates an older decided entry and leaves the new pending entry with the same id undecided

### Platform

Claude Code (merge-companion의 `manifest_io.py`는 Codex와 같은 코드)

### Operating system and shell

Windows 11 Pro (10.0.26100.9711) / PowerShell 7.7.0, Claude Code CLI 2.1.330 (데스크톱 앱), Python 3.14.7

### Summary

같은 PC에 Core를 두 번 이상 설치하면 pending-merge manifest에 **같은 id를 가진 항목이 두 개** 생길 수 있습니다. 하나는 예전 설치 때 이미 결정한 항목이고, 다른 하나는 이번 설치에서 새로 생긴 미결정 항목입니다. 이때 `mark_decided(manifest, id, "discarded")`는 **처음 찾은 항목(예전의 결정된 항목)만** 고치고 오류 없이 끝납니다. 그래서 사용자가 "버림"을 골랐는데도 새 항목은 미결정으로 남습니다. `install.ps1 -Status`는 계속 `pending-merge: pending`, `overall: warning`을 보이고, 훅은 다음 입력에도 같은 항목을 다시 보여 줍니다.

### Steps to reproduce

실제로 겪은 순서:

1. 2026-11-12에 Core를 설치했을 때 설치기가 `~/.claude/skills/synced/`(Claude 데스크톱 앱이 동기화하는 스킬 폴더)의 파일 221개를 사용자 변경으로 보고 백업했습니다. manifest에는 이 221건이 `created_at 2026-11-12`, `decided=true`, `decision=discarded`로 남아 있습니다.
2. 2026-11-24에 기준 commit `c6019ac`(`test-baseline-2026-11-16`)와 Autopilot `v0.5.1`로 다시 설치합니다: `.\install.ps1 -Platform claude -AddonSource <ghost-alice-autopilot 폴더>`. 같은 221개 파일이 다시 미결정 항목으로 등록됩니다.
3. merge-companion 절차대로 미결정 221건마다 `mark_decided(manifest, id, "discarded")`를 호출합니다.
4. `list_pending(manifest)`와 `install.ps1 -Platform claude -Status`를 봅니다.

최소 재현(실제 `~/.ghost-alice`는 건드리지 않음):

```powershell
python project-docs/reports/sk/evidence/2026-11-24/repro_mark_decided_duplicate_id.py $HOME\ghost-alice\merge-companion\scripts
```

스크립트는 임시 폴더에 항목 두 개(같은 id, 하나는 `decided=true`, 하나는 `decided=false`)짜리 manifest를 만들고 `mark_decided`를 한 번 호출합니다. 미결정 항목이 남으면 종료 코드 1로 끝납니다.

### Expected behavior

`mark_decided`가 미결정 항목을 결정 상태로 바꿉니다. 어느 항목을 바꿀지 정할 수 없으면 오류를 내서 호출자가 알게 합니다.

### Actual behavior

- 실제 manifest(`~/.ghost-alice/pending-merges/claude/manifest.json`): 항목 442개, 고유 id 249개, 두 번 나오는 id 193개.
  - 미결정 221건에 `mark_decided`를 호출했더니, 실제로 결정 상태가 된 것은 id가 겹치지 않는 28건뿐이었습니다.
  - 나머지 193건은 모두 성공으로 끝났지만 미결정으로 남았습니다(`pending after: 193`). 대신 11-12의 같은 id 항목이 다시 `discarded`로 쓰였습니다(원래 값과 같아 내용 변화는 없음).
- 최소 재현 스크립트 결과:

| Core | 결과 |
| --- | --- |
| `c6019ac` (기준) | `mark_decided returned without error, pending after: 1` → 재현됨 |
| `e4df73c` (최신 `main`) | `pending after: 0`, 새 항목이 `discarded`로 바뀜 → 재현 안 됨 |

### 원인

- `merge-companion/scripts/diff_collector.py`의 `_pending_entry_id()`는 id를 `platform`, `source_path`, `snapshot_hash`, `current_hash`, `change_kind`, `relative_path`의 해시로 만듭니다. 시각이 들어가지 않습니다. 그래서 같은 파일이 같은 해시로 다시 발견되면 같은 id가 나옵니다.
- `manifest_io.append_entry_if_absent()`는 **미결정 항목하고만** 중복을 비교합니다. 이미 결정된 같은 id 항목이 있어도 새 항목을 추가합니다.
- `c6019ac`의 `manifest_io.mark_decided()`는 `for e in data["entries"]: if e["id"] == entry_id:`로 **첫 일치 항목만** 바꾸고 바로 끝납니다.
- 최신 `main`에서는 커밋 `97185bd`(2026-11-10, "Rework corrective code against current contracts")가 `mark_decided`를 고쳤습니다. 같은 id가 여럿이면 미결정 항목 하나를 고르고, 그래도 하나로 정해지지 않으면 `ambiguous entry id` 오류를 냅니다. 이 커밋은 `c6019ac`에 들어 있지 않습니다.

### Workaround

남은 미결정 항목은 같은 모듈의 `_exclusive_lock`, `read_manifest`, `write_manifest`로 `decided=false`인 항목만 골라 기록했습니다. 그 전에 원본 파일과 백업 파일이 바이트 단위로 같은지 확인하고 manifest를 백업했습니다. 그 뒤 `install.ps1 -Platform claude -Status`가 `pending-merge: clean`, `overall: ok`가 됐습니다.

### 제안

- 기준 버전을 계속 쓰는 동안: `97185bd`의 `mark_decided` 수정만 기준 브랜치(`local/v0.5.1-windows-fix`)에 옮겨 올지 정합니다.
- 별도 문제: Claude 데스크톱 앱이 관리하는 `~/.claude/skills/synced/`가 설치할 때마다 사용자 변경으로 백업됩니다. 이 폴더를 비교 대상에서 뺄지 정합니다.

### Redacted logs or output

```text
pending before: 221
recorded discarded: 221 | pending after: 193
entries 442 unique ids 249 dup ids 193
Counter({(True, 'discarded', '2026-11-12'): 221, (False, None, '2026-11-24'): 193, (True, 'discarded', '2026-11-24'): 28})
pending ids also present as decided: 193
```

### Safety checks

- [x] I removed secrets and private data from this report.
- [x] This is not a security vulnerability report.
