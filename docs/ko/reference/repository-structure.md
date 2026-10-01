# Repository Structure

언어: [🇺🇸 English](../../reference/repository-structure.md) | 🇰🇷 한국어

이 문서는 root README에서 분리된 repository map입니다. README는 간결한 public entry point로 유지하고, 이 page가 상세 구조를 담습니다.
## Contents

- [Top-Level Documents](#top-level-documents)
- [Installer And Platforms](#installer-and-platforms)
- [Shared Utilities](#shared-utilities)
- [Core Governance And Runtime Gates](#core-governance-and-runtime-gates)
- [Official Docs And Policies](#official-docs-and-policies)
- [Installed Runtime State](#installed-runtime-state)
- [Documentation Responsibility](#documentation-responsibility)


## Top-Level Documents

| Path | Role |
| --- | --- |
| [README.md](../../../README.md) | 간결한 public entry point와 documentation hub |
| [README_ko.md](../../../README_ko.md) | Korean paired public entry point |
| [AGENTS.md](../../../AGENTS.md) | project rules와 runtime gate SSOT |
| [CLAUDE.md](../../../CLAUDE.md) | Claude Code project rule entrypoint |
| [docs/index.html](../../index.html) | GitHub Pages homepage |

## Installer And Platforms

| Path | Role |
| --- | --- |
| [install.sh](../../../install.sh) | macOS, Linux, WSL, Git Bash installer entrypoint |
| [install.ps1](../../../install.ps1) | Windows PowerShell installer entrypoint |
| [install.cmd](../../../install.cmd) | Windows CMD wrapper |
| [platforms/codex/](../../../platforms/codex/) | Codex bootstrap source |
| [hooks/](../../../hooks/) | repository hook-related files |

installer 전체 architecture는 [installer architecture](./installer-architecture.md)를 봅니다.

## Shared Utilities

| Path | Role |
| --- | --- |
| [_shared/](../../../_shared/) | installers와 skills가 공유하는 helpers |
| [_shared/secrets/](../../../_shared/secrets/) | external credential lookup helpers |
| [_shared/mcp/](../../../_shared/mcp/) | MCP installation helpers |
| [scripts/](../../../scripts/) | catalog, validation, installer compatibility, public surface verification scripts |

## Core Governance And Runtime Gates

| Path | Role |
| --- | --- |
| [session-intent-analyzer/](../../../session-intent-analyzer/) | session-intent-analyzer intent state producer의 canonical path |
| [task-router/](../../../task-router/) | capability routing을 도출하는 internal core gate |
| [boundary-contract/](../../../boundary-contract/) | work boundary contract gate |
| [coding-convention/](../../../coding-convention/) | development workflow family |
| [skill-catalog/](../../../skill-catalog/) | runtime에서 사용하는 generated gate metadata |

## Official Docs And Policies

| Path | Role |
| --- | --- |
| [official-docs/derived/](../../../official-docs/derived/) | 공개 기여자가 따라야 하는 준수 절차이며 장문 철학은 Wiki에서 제공합니다 |
| [docs/policies/](../policies) | runtime, platform, evaluator policy documents |
| [docs/ko/policies/](../policies/) | Korean counterparts for policy documents |
| [docs/plans/](../plans) | public roadmap policy and planning boundaries |
| [docs/ko/plans/](../plans/) | Korean counterparts for planning documents |

## Installed Runtime State

다음 path는 repository 밖의 user home directory 아래에 있습니다.

| Path | Role |
| --- | --- |
| `~/.ghost-alice/install-state/` | platform install-state manifests |
| `~/.ghost-alice/pending-merges/` | user-modified installed files를 위한 protected queue |
| `~/.ghost-alice/uninstall-reports/` | uninstall reports |
| `~/.ghost-alice/secrets.env` | standard credential helper storage |
| `~/.agents/skills/` | Codex user skill copy install target |
| `~/.claude/skills/` | Claude Code skill install target |

Codex의 관리 부트스트랩은 완전하게 제공된 프로젝트 계약 또는 설치된 `~/.codex/ghost-alice-governance.md` 대체 계약을 선택합니다. 설치기는 `~/.codex/config.toml`에서 전체 지침 예산을 관리합니다. 세션 실행 상태의 기준은 영수증의 저장소 루트에 있는 `.tmp/session-intent/ghost-state.sqlite3`입니다. JSON 포인터와 스냅샷은 호환성·내보내기 자료이며, 상태는 영수증의 root·platform·session identity를 사용하여 `session_intent_ledger.py --read-state`로 읽습니다.

## Documentation Responsibility

- README는 public description과 quick start만 담습니다.
- 자세한 installation과 update guidance는 [installation guide](../getting-started/installation.md)에 둡니다.
- contributor-facing installer flow와 safety model은 [installer architecture](./installer-architecture.md)에 둡니다.
- Policy와 runtime matrices는 [docs/ko/policies](../policies/)에 둡니다.
- 장문 철학과 closed-loop reasoning은 [Wiki](https://github.com/AidALL/ghost-alice/wiki/Home_ko)에 두며 [official-docs/derived](../../../official-docs/derived/)는 공개 기여자 절차를 담습니다.
