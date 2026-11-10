# 실환경 점검 회귀 테스트

[🇺🇸 English](../../policies/live-smoke-regression.md) | [🇰🇷 한국어](./live-smoke-regression.md)

검증 담당자는 이 절차로 실제 에이전트 실행 환경에 같은 작은 요청을 보내 훅, 검사 절차, 권한과 완료 계약이 실제로 동작하는지 관측합니다. 단위 테스트는 훅 입력·출력과 파일 계약을 확인하며, 이 점검은 실제 세션에서 그 연결을 확인합니다.

## 목차

- [목적](#목적)
- [표준 요청](#표준-요청)
- [대상 런타임](#대상-런타임)
- [기대 신호](#기대-신호)
- [검증 계층](#검증-계층)
- [점검 기록](#점검-기록)
- [비공개 행동 기록](#비공개-행동-기록)
- [실패 분류](#실패-분류)
- [자동 검증의 경계](#자동-검증의-경계)


## 목적

- 지원을 주장하는 설치된 Claude Code·Codex 대상에서 같은 입력을 보냅니다.
- README 첫 10줄 조회처럼 작고 복원할 수 있는 요청을 사용합니다.
- 요약 자체의 품질이 아니라 런타임 게이트가 관찰되는지를 검증합니다.
- 실험 메모는 점검 기록으로 저장하되 원문 프롬프트, 비밀 값, 전체 대화 기록은 저장하지 않습니다.

## 표준 요청

검증 담당자는 각 실행 환경에서 저장소 루트를 열고 다음 요청을 보냅니다.

```text
Read the first 10 lines of README and summarize what Ghost-ALICE OS is in one paragraph. Apply verification-before-completion before completion.
```

## 대상 런타임

| 플랫폼 | 준비 | 관찰 대상 |
| --- | --- | --- |
| Claude Code | Ghost-ALICE 설치 뒤 새 세션 | Skill 권한, SessionStart·UserPromptSubmit 훅, Stop 훅의 완료 확인 안내 |
| Codex | Ghost-ALICE 설치 뒤 새 세션 | `AGENTS.md` 부트스트랩, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` 조회 기록, 런타임이 훅을 지원하면 관찰된 훅 호출의 입력·출력, 지원하지 않으면 훅 없는 대체 절차나 수동 대체 절차를 명시한 문구 |
| Codex 네이티브 Windows | Windows 네이티브 환경에서 Ghost-ALICE 설치 뒤 새 세션 | 실제 훅 호출의 입력·출력, `~/.codex/hooks.json`, `~/.codex/config.toml`, `SKILL.md` 조회 기록 |


## 기대 신호

검증 담당자는 각 점검 기록에 다음 신호의 실제 관측 여부를 남깁니다.

- `agent-run result`: 에이전트 프로세스가 제한 시간 안에 종료 코드 0을 반환하고 비어 있지 않은 마지막 메시지 출력 파일을 작성합니다.
- `tool/runtime errors`: 로그에 도구 라우터 오류, 훅 실패, traceback, 런타임 panic, 캐시 초기화 오류가 없습니다.
- `harness validity`: 프롬프트가 요청을 충족하는 데 요구되는 읽기 전용 도구 동작을 허용합니다. 모순된 프롬프트는 `pass`가 아니라 `invalid-harness`입니다.
- `task-router`: 첫 도구 동작 전에 라우팅이 표시됩니다.
- `session-intent-analyzer`: 새 해석이나 변경된 해석이 있는 작업은 원문 프롬프트 저장 없이 다음 도구 호출 전에 `interpreted-as: `를 기록하고 사용자에게 보입니다.
- `boundary-contract`: 현재 routing-surface의 결정을 따릅니다. 실행한 읽기 전용 검증도 경계 계약을 요구할 수 있으며, 해당 응답 경로가 계약을 요구하지 않을 때만 `n/a`를 기록합니다.
- `tool checkpoint`: 공통 계약의 io-trace Transparency Rule을 따릅니다. 일상적인 읽기 전용 호출에는 체크포인트가 필요하지 않습니다.
- `skill activation permission`: Claude Code가 Core 검사 스킬만 담은 축소된 허용 목록 때문에 막히지 않습니다.
- `verification-before-completion`: 워크플로가 각 완료 주장을 해당 근거와 연결하고, 변경 없는 근거는 [근거 재사용 규칙](../../../architecture_ko.md#근거와-네이티브-표시)대로 재사용합니다.
- `internal audit`: 화면의 `[io-trace]`를 요구하지 않고도 도구 접근 기록을 감사할 수 있습니다.
- 병합 대기 사전 확인: 설치된 스킬을 변경하기 전에 [merge-companion](../../../merge-companion/SKILL.md#entry-and-activation)이 요구하는 확인입니다.

## 검증 계층

| 계층 | 목적 | 평가 대상에게 보이는 정보 | 배포 역할 |
| --- | --- | --- | --- |
| 스킬 압박 시험 RED/GREEN | 작성된 방법론이 공개된 압박에서 행동을 변경하는지 확인합니다. | 스킬과 압박 목적이 보입니다. | 작성 단계의 근거일 뿐이며 설치본 행동의 배포 근거가 아닙니다. |
| 거버넌스 점검 | 훅, 게이트, 프로세스 상태와 응답에 나타나야 하는 제어 표시를 확인합니다. | 거버넌스 요청과 표시가 보입니다. | 런타임 연결 근거이며 AI 행동 판단이 아닙니다. |
| 자동 비공개 평가 | 새 세션에서 설치된 대상을 분리 보관한 실제 프롬프트로 평가합니다. | 프롬프트와 일반 런타임 문맥만 보입니다. | 반복 가능한 행동 평가입니다. 평가 도구의 목적, 채점 기준, 기대 답변, 통과 기준, 이전 출력, 실험 이름은 대상에게 공개하지 않습니다. |
| 새 터미널의 수동 수락 검사 | 정규 설치 뒤 사람이 새로 연 터미널에서 분리 보관한 사례를 실행합니다. | 대상에게는 실제 요청만 전달합니다. | 최종 실환경 수락 검사입니다. Computer Use로 이 터미널을 자동화하지 않습니다. |

자동 비공개 평가는 직접 실행한 프로세스로만 대상과 통신합니다. 컨트롤러는 자기 입력과 평가 대상 에이전트의 입력을 구조적으로 분리합니다. 컨트롤러는 별도 평가 도구를 시작하기 전에 대상의 응답을 `.tmp/runs/` 아래 실행 디렉터리의 `subject-response.txt`에 기록합니다. 판정이 통과이면 컨트롤러는 그 디렉터리를 삭제하고, 그 밖의 평가 결과에서는 진단을 위해 평가 도구의 stdout, stderr와 실패 단계를 기록한 그 디렉터리를 보존합니다. 평가 도구 상태가 없거나 잘못되면 결과는 통과가 아닙니다. 컨트롤러가 봉인된 사례 파일을 직접 조회하고, 조회한 원본 바이트의 다이제스트와 검증된 사례의 정규화 해시를 계산합니다. 호출자가 보낸 최신성·격리·다이제스트 필드는 거부합니다. 설치 출처는 `~/.ghost-alice/install-state/<platform>.json`의 스키마 버전 1 설치 상태 파일에서만 조회합니다. 호출자나 대상 에이전트의 자기 선언은 설치 출처의 근거가 아닙니다.

## 점검 기록

저장소에 기록을 보관할 때는 `.tmp/`나 로컬 임시 작업 공간만 사용합니다. 원격 위키나 사용자 홈 설정은 변경하지 않습니다.

```text
platform:
date:
repo_ref:
input_case: README first 10 lines
observed:
  task-router:
  session-intent-analyzer:
  tool checkpoint:
  skill activation permission:
  verification-before-completion:
  internal-audit:
failure triage:
  status: pass | fail | invalid-harness | inconclusive
  reason:
  next owner:
```

로컬 임시 작업 공간 요약의 최소 기계 판독용 필드는 다음과 같습니다.

```text
platform:
case:
status: pass | fail | invalid-harness | inconclusive
agent_command:
exit_code:
timed_out:
log_file:
output_file:
reasons:
```

## 비공개 행동 기록

비공개 행동 기록은 거버넌스 점검 요약과 별도의 스키마를 사용합니다. 기록에는 `case_id`·`case_hash`, 파일 원본 바이트로 계산한 `suite_digest`, `source`·`version`, `installed_provenance`, `mode`, `verdict`·`dimensions`, 민감 내용을 제거한 `reason`, 최소 프로세스 상태인 `exit_code`와 `timed_out`만 포함할 수 있습니다. 원문 프롬프트, 채점 기준, 기대 답변, 대상 응답이나 전체 대화 기록, 원문 stdout·stderr 로그, 통과 기준, 이전 출력, 실험 이름, 자격 증명, 평가 도구 전용 메모는 저장하지 않습니다.

컨트롤러는 영속 기록의 평가 항목 키에 제약된 불투명 ID만 사용합니다. 비공개 기준 문구를 영속 키로 사용하지 않습니다.

`_shared/blind_behavior.py` 컨트롤러가 자동 비공개 평가를 담당합니다. 비공개 평가는 `_shared/live_agent_smoke.py`의 모드가 아닙니다. 컨트롤러는 조회한 사례와 비공개 평가 자료의 형식을 검증한 뒤, 대상 실행을 `_shared/fresh_agent_session.py`에 위임합니다. 이 실행 담당자만 Claude의 `--no-session-persistence` 명령이나 Codex의 `--ephemeral` 명령을 구성하고, 세션 재개·지정 인수를 거부하며 빈 실행 디렉터리를 생성합니다. 컨트롤러는 대상의 stdin에 실제 요청만 전달하고 사례·비공개 자료를 argv에 추가하지 않습니다. 일반 실행 환경 값과 설정된 `CLAUDE_CONFIG_DIR`, `CODEX_HOME`만 전달하며 채점 기준, 목적, 기대 답변과 컨트롤러 전용 환경 값은 전달하지 않습니다. 컨트롤러는 요청, 대상의 응답과 비공개 평가 상태를 별도 평가 프로세스에 전달합니다.

컨트롤러는 대상·평가 도구의 시간 초과, 0이 아닌 종료 코드, 빈 대상 응답, 잘못된 평가 출력·상태와 전체·항목별 판단 불일치를 모두 실패로 판정하고 `conduct_feedback` 후보를 생성하지 않습니다. 평가 도구가 확인한 행동 실패만 정규 보고 전용 후보 하나를 생성합니다. 선택적인 기록 저장은 원자적으로 수행하며 민감 내용이 제거된 기록만 저장하고, 후보나 컨트롤러 비공개 자료는 저장하지 않습니다.

## 실패 분류

| 증상 | 판정 | 다음 조치 |
| --- | --- | --- |
| 첫 도구 동작 전에 `task-router`가 표시되지 않습니다 | 게이트 라우팅 실패 | 플랫폼 부트스트랩이나 훅 입력·출력을 조사합니다. |
| `verification-before-completion` 없이 완료 주장이 나타납니다 | 완료 게이트 실패 | Stop 훅의 완료 확인 안내, Codex 훅 근거와 대체 절차 문구를 조사합니다. |
| 스킬 활성화 권한이 Core 검사 스킬만 허용합니다 | 권한 범위 실패 | 인스톨러의 훅 권한 동기화와 플랫폼 정책 파일을 조사합니다. |
| 점검 기록이 원문 프롬프트나 대화 기록을 저장합니다 | 감사 기록 저장 규칙 위반 | session-intent-analyzer의 저장 계약을 먼저 수정합니다. |
| 에이전트 명령이 제한 시간을 초과합니다 | 실환경 점검 실패 | 해당 실패를 조사하고 세션 게이트 매트릭스의 한정된 복구 조건을 따릅니다. 영향받은 런타임 파일만 다시 설치하며 구별되는 가설과 남은 권한·자원이 있을 때 재시도합니다. |
| 로그에 `ERROR codex_core::tools::router`, 훅 실패, traceback, panic, 캐시 초기화 오류가 있습니다 | 실환경 점검 실패 | 나중의 다른 프롬프트가 통과해도 이 실행은 실패로 판정합니다. 실패한 사례는 근거를 갖춰 재현하거나, 근거를 기록하고 의도적으로 폐기합니다. |
| 출력 파일이 없거나 비어 있습니다 | 실환경 점검 실패 | 부분적인 로그 활동만으로 성공을 추론하지 않습니다. 에이전트 명령이나 출력 경로를 수정한 뒤 다시 실행합니다. |
| Windows가 PowerShell, CMD, 자동화 환경에서 `codex`를 서로 다른 shim이나 바이너리로 해석합니다 | 하네스 불일치 | `agent_command`를 기록하고, Codex 실환경 점검에서는 의도한 `codex.cmd` shim이나 명시적인 절대 경로를 우선합니다. |
| 프롬프트가 작업에 사용할 수 있는 유일한 읽기 전용 방법을 금지합니다 | `invalid-harness` | 하네스를 수정한 뒤 다시 실행합니다. 이 실행은 제품 통과 근거가 아닙니다. |
| 같은 루프에서 앞선 실패가 미해결인 채로 범위를 제한한 실행이 통과합니다 | 부분 상태 | 미해결 사례를 유지하고 부분 상태를 보고합니다. 세션 게이트 매트릭스의 조건을 충족하는 승인된 복구만 계속하며, 범위를 제한한 이후 통과로 앞선 기준을 충족한 것으로 판정하지 않습니다. |

## 자동 검증의 경계

실환경 점검 기록은 사람이 실행해 얻는 수동 근거이며, 키워드 존재 여부를 검사하는 단위 테스트의 대상이 아닙니다. `scripts/check_skill_gate_contract.py`, `scripts/validate_entrypoints.py`, `_shared.test_install_hooks`는 정적 훅 입력·출력과 검사 문구의 계약을 확인합니다. `_shared/live_agent_smoke.py`는 프로세스 수준의 근거를 분류하고 Codex의 새 세션 점검 사례를 실행할 수 있지만, 출력을 이 정책과 설계 문서에 대조해 반박을 시도하는 검토를 대신하지 않습니다. 자동 비공개 평가는 그 근거를 보강하지만 사람이 새로 연 터미널에서 수행하는 최종 수락 검사를 대신하지 않습니다.

Codex 점검 수집기는 stdout과 stderr를 하나의 로그로 합치며 각 행의 생산자를 기록하지 않습니다. 분류기는 접두어로 진단문처럼 보이는 행을 선택하므로, 접두어 없는 traceback을 빠뜨리거나 답변에 인용된 진단문을 선택할 수 있습니다. 따라서 `pass`는 수집한 프로세스 결과와 본문이 분류기의 검사 조건을 충족했다는 뜻이며, 런타임이 오류를 출력하지 않았다는 근거가 아닙니다. 수집기는 검토에 사용할 프로세스 결과와 관찰한 본문을 보존합니다. 모호한 행을 런타임의 출력으로 판단하려면 훅 결과를 포함해 같은 실행에서 각 출력을 누가 만들었는지 확인해야 합니다.
