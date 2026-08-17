# Ghost-ALICE OS v0.2.2 릴리스 노트

날짜: 2026-08-17

범위: `v0.2.2`는 `v0.2.1` 이후 준비된 governance 동작, Claude/Codex 설치 호환성, repository-local 실행 격리, 목적 비노출 installed-agent 평가, Windows junction 처리, release verification surface를 갱신한다.

상태: 이 파일은 `v0.2.2` release body의 기준이며, 공개 release body는 이 파일 및 `CHANGELOG.md`와 동기화해야 한다.

## 주요 변경

- primary request와 causal response order를 보존하고 terminal objective를 requested means보다 우선하며 decision-relevant uncertainty와 state delta가 없을 때 verification을 중단한다.
- managed Claude global bootstrap의 설치, status, refresh, permission, uninstall 동작을 Codex global bootstrap과 맞춘다.
- child temp, Python cache, clean working directory, failed-run diagnostics가 소유 repository의 `.tmp` 아래에 남도록 repository-local child runtime과 canonical test launcher를 추가한다.
- controller가 held-out case를 직접 읽고 exact content에 evidence를 결합하며 fresh ephemeral Claude/Codex session만 실행하고 evaluator-only rubric과 purpose를 subject에 노출하지 않는 blind behavior evaluation을 추가한다.
- install-mode detection, collision proof, safe provisioning, content hash, dangling-target repair, uninstall fixture, cross-shell test 전반에서 Windows addon junction 처리를 강화한다.
- width 기반 prose wrapping과 installer progress padding을 제거하고 structural Markdown wrapping gate를 추가하며 semantic output을 truncation 없이 보존한다.
- 중복 verification 및 hookless-fallback owner를 제거하고 adversarial convergence를 decision value로 제한하며 Claude full-uninstall rule removal을 shared report owner로 통합한다.

## 검증 범위

- method-level progress가 활성화된 상태에서 `python scripts/run_installer_compat_tests.py --group shared-all`과 `python scripts/run_installer_compat_tests.py --group scripts-all`을 실행한다.
- public-surface, skill-contract, skill, platform-adapter, entrypoint, prose-wrap, merge-companion validator를 실행한다.
- Claude와 Codex에 core 및 호환 autopilot addon을 설치하고 status와 doctor를 확인한 뒤 behavioral evaluation을 실행한다.
- evaluator-visible smoke와 installed Claude/Codex blind-controller case를 분리하며, hidden-purpose behavioral release evidence에는 후자만 사용한다.

## 호환성 경계

- 이 core와 짝을 이루는 공식 `ghost-alice-autopilot` addon release는 `v0.1.2`이며 minimum core version은 `0.2.2`다.
- platform claim은 repository compatibility matrix와 실제 installed-session evidence 범위로 제한한다.

## 릴리스 경계

- 공개 완료 판단 전에 repository `VERSION`, Git tag, changelog section, release body가 모두 `v0.2.2`를 식별해야 하며, installed status는 installer-owned source linkage와 설치된 각 platform의 managed global bootstrap이 정상임을 보고해야 한다.
