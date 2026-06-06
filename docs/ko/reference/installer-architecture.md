# Installer Architecture

언어: [🇺🇸 English](../../reference/installer-architecture.md) | 🇰🇷 한국어

이 문서는 빠른 설치 문서와 installer compatibility contract 사이의 contributor bridge다. installer 전체가 단순하다고 주장하지 않는다. contributor가 installer, hook payload, install state, test를 따라가기 전에 필요한 architecture map을 제공한다.

빠른 사용자용 절차는 [installation](../getting-started/installation.md)을 본다. compatibility requirement와 test ownership은 [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md)를 본다.
## Contents

- [읽는 순서](#읽는-순서)
- [Architecture Summary](#architecture-summary)
- [Install Flow](#install-flow)
- [Platform Surfaces](#platform-surfaces)
- [State And Safety Model](#state-and-safety-model)
- [Contributor Entry Points](#contributor-entry-points)
- [What Not To Infer](#what-not-to-infer)
- [Full Understanding](#full-understanding)


## 읽는 순서

1. 이 문서에서 구성 요소와 호출 방향을 파악한다.
2. file 위치는 [repository structure](./repository-structure.md)에서 찾는다.
3. 사용자-facing behavior는 [installation](../getting-started/installation.md)과 [uninstall](../getting-started/uninstall.md)에서 확인한다.
4. contract와 verification burden은 [installer platform compatibility](../policies/installer-platform-compatibility-matrix.md), [tool output semantics](../policies/tool-output-semantics.md), [live smoke regression](../policies/live-smoke-regression.md)에서 확인한다.

## Architecture Summary

installer는 세 layer로 볼 수 있다.

| Layer | Main files | Responsibility |
| --- | --- | --- |
| Shell entrypoints | `install.sh`, `install.ps1`, `install.cmd` | platform-specific flags를 parse하고 encoding/runtime setup을 normalize한 뒤 Python-backed installer path로 진입한다 |
| Installer orchestration | `_shared/install_hooks.py`, `installer_lib/`, `_shared/install_transaction.py`, `_shared/install_state_writer.py` | platforms와 targets를 resolve하고, skills와 hooks를 install하고, install state를 record하고, local edits를 protect하고, status를 report한다 |
| Runtime surfaces | `~/.claude/`, `~/.codex/`, `~/.agents/skills/`, `~/.ghost-alice/` | installed skills, platform hook config, Codex bootstrap instructions, install-state manifests, hook feature rollback metadata, pending merges, uninstall reports를 보관한다 |

중요한 mental model은 "file을 copy하고 끝"이 아니다. installer는 stateful
governance synchronizer다. installed asset을 update하고, hook contract를
verify하고, user-modified installed file을 보존하고, status, doctor, update,
uninstall path가 무엇이 일어났는지 판단할 수 있도록 state를 남긴다.

## Install Flow

```text
shell entrypoint
-> runtime and source-health preflight
-> platform and skill target resolution
-> skill and shared-helper copy install
-> platform hook and bootstrap sync
-> install-state and event recording
-> status/report output
```

`install.cmd`는 Windows wrapper일 뿐이다. 실질적인 Windows path는
`install.ps1`이다. Unix-like environment에서는 `install.sh`가 shell entry
path를 소유하고 필요할 때 Bash로 다시 진입한다.

## Platform Surfaces

| Platform | Installed surfaces |
| --- | --- |
| Claude Code | `~/.claude/skills/`, `~/.claude/settings.json`, Claude command wrappers |
| Codex | `~/.agents/skills/`, `~/.codex/AGENTS.md`, `~/.codex/hooks.json`, `~/.codex/config.toml` |
| Shared state | `~/.ghost-alice/install-state/`, `~/.ghost-alice/pending-merges/`, `~/.ghost-alice/uninstall-reports/`, `~/.ghost-alice/install/` |

Claude Code는 native skill invocation과 hook permissions를 expose할 수 있다.
Codex는 같은 skill surface를 expose하지 않으므로 installer는 Codex bootstrap과
hook config도 설치해 required gate가 `SKILL.md` read record와 hook payload로
audit 가능하도록 한다.

## State And Safety Model

installer는 cleanup 때 추측하지 않기 위해 변경 내용을 기록한다.

| State path | Why it exists |
| --- | --- |
| `~/.ghost-alice/install-state/<platform>.json` | status와 uninstall을 위해 installer-owned targets와 environment changes를 기록한다 |
| `~/.ghost-alice/pending-merges/<platform>/` | silently overwrite할 수 없는 user-modified installed files를 보존한다 |
| `~/.ghost-alice/install/` | audit와 recovery를 위해 install reports와 event traces를 보관한다 |

이 때문에 update와 uninstall은 installer architecture 밖의 별도 작업이 아니다. copied asset, hook, runtime config에 영향을 주는 change는 보통 status, doctor, pending merge behavior, uninstall rollback에도 영향을 준다.

## Contributor Entry Points

| Change type | Start here | Then verify with |
| --- | --- | --- |
| Shell flag or platform bootstrap | `install.sh`, `install.ps1`, `install.cmd` | shell parser tests, runtime detection tests, compatibility matrix groups |
| Hook payload or hook status wording | `_shared/install_hooks.py` | `_shared.test_install_hooks`, `scripts/check_skill_gate_contract.py` |
| Install-state or uninstall behavior | `_shared/install_state_writer.py`, `_shared/uninstall_cleanup.py`, `installer_lib/` | install-state schema tests and uninstall tests |
| Public docs or command surface | `README.md`, `docs/`, `docs/index.html` | `scripts/validate_public_surfaces.py` and public-surface contract tests |
| Platform compatibility rule | `docs/policies/installer-platform-compatibility-matrix.md` | `scripts/run_installer_compat_tests.py --list` and policy가 지정한 focused group |

한 부분을 바꿀 때는 paired contract를 찾는다. compatibility matrix가 이름 붙인 test owner가 regression surface를 가장 빨리 찾는 길이다.

## What Not To Infer

- hook file이 disk에 존재한다는 사실은 hook payload가 current라는 증거가 아니다.
- copied skill directory는 platform이 그 skill을 invoke하거나 audit할 수 있다는 증거가 아니다.
- passing install smoke는 status, doctor, update, pending merge, uninstall path가 정렬됐다는 증거가 아니다.
- user-facing install command는 architecture 설명이 아니라 entrypoint일 뿐이다.

## Full Understanding

contributor는 보통 몇 분 안에 방향을 잡을 수 있다. 그러나 installer 전체를 이해하려면 하나의 change를 entrypoint, orchestration layer, runtime surface, state file, test owner까지 따라가야 한다. 이 문서는 그 trace를 위한 첫 map이다. 관련 implementation과 compatibility contract를 읽는 일을 대체하지 않는다.
