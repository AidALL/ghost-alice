# Ghost-ALICE OS v0.2.1 릴리즈 노트

Date: 2026-07-01

Scope: `v0.2.0` 이후 현재 `main` 기준이며, `v0.2.1` 태그를 위한 거버넌스 런타임 하드닝, acceptance-criteria lifecycle 변경, cross-platform io-trace/runtime diagnostic 보강을 포함한다.

Status: 릴리즈 문서가 `v0.2.1` 태그 준비용으로 작성되었다. GitHub Release 발행은 브랜치, PR, CI, merge 흐름 이후 진행한다.

## Main Changes

- completion-check 및 install-doctor 프롬프트에 대한 live Codex smoke coverage를 추가하고, command-resolution 및 hook-trust compatibility 처리를 포함했다.
- 설치된 runtime shared file, hook runner dependency, partial install, session-intent dependency 누락이 명확한 doctor diagnostic으로 드러나도록 강화했다.
- acceptance-criteria lifecycle state를 추가하고, 이미 충족된 criterion의 `met_at`이 재병합 때 보존되도록 했다.
- 한국어 후치 부정, negated status text, 이후 executed-work success claim 주변 completion-check validation을 강화했다.
- 로컬 audit record에는 raw command를 보존하면서 Bash io-trace `op`/`path` 구조화를 추가했다.
- Windows reparse point uninstall, io-trace path normalization, cross-platform installer 및 hook surface의 line-ending coverage를 개선했다.
- transient router diagnostic에서 회복한 완전한 governed run은 false-fail하지 않고, governance marker가 빠진 incomplete run은 계속 실패로 분류하도록 live smoke classification을 조정했다.

## Verification Surface

- 로컬 unit verification은 `_shared` tests, `scripts/tests`, public surface validators, installer compatibility checks, platform adapter validation을 포함해야 한다.
- Korean public-doc counterpart validation은 이 파일 `docs/ko/release/2026-07-01-release-notes.md`를 포함해야 한다.
- Live Codex smoke verification은 설치된 runtime 기준 `completion-check-readme` 및 `install-doctor-read` cases를 포함해야 한다.
- `v0.2.1` 발행 전 release PR에서 GitHub Actions가 통과해야 한다.

## Compatibility Boundary

- 이 릴리즈는 Codex 및 Windows runtime diagnostics를 개선하지만, public compatibility claim을 repository compatibility policies 밖으로 확장하지 않는다.
- 공식 autopilot addon은 별도 `ghost-alice-autopilot` repository에서 versioning 및 release를 독립적으로 수행한다.

## Release Boundary

- 이 노트 자체는 tag 또는 GitHub Release를 생성하지 않는다.
- `v0.2.1` release body의 source로 이 문서와 `CHANGELOG.md`를 사용한다.
