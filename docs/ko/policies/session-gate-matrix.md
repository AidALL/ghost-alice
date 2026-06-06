# Session Gate Matrix

언어: [🇺🇸 English](../../policies/session-gate-matrix.md) | 🇰🇷 한국어

session gate SSOT는 `skill-catalog/session-gates.json`이다. 이 문서는 human-readable matrix다.
## Contents

- [Required Entrypoints](#required-entrypoints)
- [Turn Routing Contract](#turn-routing-contract)
- [Session Intent Ledger Contract](#session-intent-ledger-contract)
- [Runtime Hook Graph Contract](#runtime-hook-graph-contract)
- [Dynamic Focus Contract](#dynamic-focus-contract)
- [Work-Impact Projection Contract](#work-impact-projection-contract)
- [Runtime Checkpoints](#runtime-checkpoints)
- [tool-checkpoint Visible Surface](#tool-checkpoint-visible-surface)
- [tool-checkpoint Batch / Continuation Compression](#tool-checkpoint-batch-continuation-compression)
- [Notes](#notes)


## Required Entrypoints

| Situation | Required entrypoint |
| --- | --- |
| 모든 user input의 first intake | `session-intent-analyzer` |
| session-intent intake와 jailbreak-detector downstream gate 이후, downstream work/tool calls 전 첫 agent routing step | `task-router` |
| development/coding request | `coding-convention/using-coding-convention` |
| bug fix | `coding-convention/systematic-debugging` |
| production code change | `coding-convention/test-driven-development` |
| non-empty final response, including completion claims, recommendations, choices, success judgments | `coding-convention/verification-before-completion` |
| commit/push 직전 | `coding-convention/finishing-a-development-branch` |
| 새 task, sub-task, follow-up item 정의 시 | `necessity-gate` |
| task-router가 `boundary-contract: required`를 route한 직후 | `boundary-contract` |

## Turn Routing Contract

session-intent-analyzer intake가 first다. pending-merge precheck 이후 session-intent-analyzer는 `skill-evolution` (report-only terminal branch)와 `jailbreak-detector`로 fan out한다. current-lineage block gate가 없으면 session-intent preflight 뒤 task-router가 release된다. allow turn에서는 `downstream-gates.json`이 없을 수 있으며, current-lineage model block이 기록되지 않았다면 그 absence는 silent allow다. flow를 `task-router → session-intent-analyzer`, linear `session-intent-analyzer → skill-evolution → jailbreak-detector → task-router` chain, 또는 jailbreak-detector downstream gate를 bypass하는 task-router로 설명하면 contract violation이다.

`task-router`는 일이 커 보일 때만 쓰는 classifier가 아니다. user input이 감지된 모든 turn에서 session-intent-analyzer intake와 jailbreak-detector downstream gate 이후, downstream work 또는 tool call 전에 적용한다.

`task-router`는 `session-intent-analyzer`와 `jailbreak-detector/downstream-gates.json` context의 consumer이며 agent-side request decomposition step이다. user-input intake, raw intent inference, ledger updates, jailbreak decisions, downstream gate state, tool permission을 소유하지 않는다.

task-router reminder hook은 session-intent preflight가 존재하고 current-lineage block check가 run될 수 있을 때까지 task-router를 withhold해야 한다. current-lineage block gate가 없으면 absent `downstream-gates.json`은 silent allow다. release 이후 task-router는 session intent ledger를 읽고 atomic meaning decomposition을 수행한 뒤 output, verification, lifecycle, boundary skills를 assign한다.

다음은 모두 fresh routing targets다.

- simple questions
- opinions
- status comments
- previous work의 follow-up questions
- "is this right?" 같은 judgment requests
- quick checks or smoke tests

previous turn이 routed 됐다는 사실은 next turn의 evidence가 아니다. hook payload가 current platform pending-merge precheck가 run됐고 pending warning이 없다는 contract를 제공하면 `merge-companion-precheck: clean (hook-verified)`를 기록하고 shell manifest check를 반복하지 않는다. hook evidence가 없거나 hookless/manual environment일 때만 current platform manifest를 직접 읽는다.

runtime smoke는 `docs/ko/policies/live-smoke-regression.md`를 따른다. 이 procedure는 동일한 README first 10 lines input을 Claude Code, Codex, Antigravity로 보내 `task-router`, `verification-before-completion`, concise tool-checkpoint failure surface, skill activation permission signal을 관찰한다.

## Session Intent Ledger Contract

`session-intent-analyzer`는 모든 user input마다 session intent ledger를 update한다. Hooks는 input digest와 event metadata만 저장하며 raw prompts는 절대 저장하지 않는다. user goal, constraints, decisions, non-goals가 바뀌면 agent는 compressed delta를 `intent-state.json`에 기록한다.

`intent-state.json`은 update-plus-accumulate state다. `current_goal`, `user_intent_summary` 같은 latest scalar intent fields는 newer semantic deltas로 replace되고, constraints, non-goals, open questions, acceptance criteria, consumer hints, active decisions 같은 list-like fields는 stable identity 또는 deduped value로 merge된다. `intent-events.jsonl`은 append-only audit metadata이며 raw prompt transcript가 아니다.

- Storage location: Ghost-ALICE repo root 아래 `.tmp/session-intent/<platform>/<session-id>/`
- Consumers: `skill-evolution`, `jailbreak-detector`
- Forbidden: raw prompt, full conversation, tool output, system/developer instruction text, secret values
- Hookless/manual environment: first response 전에 session-intent-analyzer storage contract를 manual로 적용한다.

First-entry intake invariant:

- every user input은 먼저 session-intent intake path에 연결된다.
- pending-merge precheck 이후 session-intent-analyzer는 `skill-evolution` (report-only terminal branch)와 `jailbreak-detector`로 fan out한다. task-router는 session-intent preflight와 current-lineage block gate absence 이후에만 따른다.
- `skill-evolution`은 session-intent-analyzer의 report-only terminal branch이며 task-router로 feed하지 않는다.
- missing `current-session.json`, `intent-state.json`, hook payload, preflight evidence, semantic delta evidence는 first entry deny reason이 아니다.
- missing session-intent evidence는 intake/bootstrap이 run 또는 continue되어야 한다는 뜻이지 tool-checkpoint가 absence에서 risk를 infer할 수 있다는 뜻이 아니다.
- tool-checkpoint는 intake 전에 user's next input을 predict하거나 unknown intent를 block condition으로 취급하면 안 된다.

## Runtime Hook Graph Contract

pending-merge precheck는 user-input governance graph가 시작되기 전에 run한다. 이는 pre-routing/session-start layer이며 `session-intent-analyzer`, `skill-evolution`, `jailbreak-detector`, `tool-checkpoint`가 여는 downstream gate가 아니다. undecided entry가 있으면 runtime은 `merge-companion`을 먼저 surface해야 하지만 user-explicit defer/skip은 해당 entry를 undecided로 남긴 채 계속 진행할 수 있다.

pending-merge precheck가 clean이거나 user가 명시적으로 defer한 뒤 user-input governance graph는 user intent first, downstream gate state second, tool-stage tool-checkpoint third 순서로 정렬된다.

`tool-checkpoint`는 PreToolUse/BeforeTool checkpoint이며 user-input intake order의 일부가 아니다. surfaced될 때 visible control schema에는 `hook-stage: PreToolUse`와 `meaning: tool-call retry checkpoint, not user-input intake`가 함께 있어야 한다.

1. user input은 every turn에서 `session-intent-analyzer`를 trigger한다. hook은 input digest, session ledger, `current-session.json` pointer를 쓰고 agent turn을 allow한다.
2. `skill-evolution`과 `jailbreak-detector`는 platform과 session id로 keyed된 같은 session temp files를 consume한다. skill-evolution은 report-only and self-terminating이며 downstream gates를 open/close하지 않고 task-router로 feed하지 않는다.
3. jailbreak-detector는 ledger에 `model_security_decision`을 기록하고 current-lineage block decisions만 downstream gateway인 `downstream-gates.json`으로 carry한다. deterministic hard-block rules는 explicit high-confidence attack signals에 대한 narrow regression guards이며 모든 jailbreak attempts를 block한다는 proof가 아니다. gate block은 deterministic text matching이 아니라 model-recorded judgment (`model_security_decision`)에서만 derive된다. gradual multi-turn jailbreak resistance는 session-intent summary quality와 cumulative constraint comparison에 달려 있다. current-lineage block gate가 있으면 tool execution은 tool-shape review 전에 deny된다. current block gate가 없으면 absent `downstream-gates.json`은 silent allow다.
4. task-router reminder hook은 session-intent preflight가 존재하고 current-lineage block check가 first chance를 가질 때까지 기다린다. `opened=false` 또는 `decision=block`인 current-lineage block gate가 있으면 task-router를 withhold하고, absent `downstream-gates.json`은 silent allow다. release 이후 `task-router`가 current-session pointer와 session intent ledger를 읽고 accepted intent를 atomic meaning units로 decompose한 뒤 output, verification, lifecycle, boundary skills를 assign한다. raw user intent를 infer하거나 tool permission을 decide하지 않는다.
5. `tool-checkpoint`는 session-intent-analyzer intake와 jailbreak-detector downstream gate가 first chance를 가진 뒤 tool stage에서 run한다. missing intake evidence는 bootstrap/recovery를 시작할 이유이지 `task-router`를 `session-intent-analyzer`나 `jailbreak-detector`보다 앞세울 이유가 아니다. runtime decision은 current-lineage block gate만 읽는다. `opened=false` 또는 `decision=block`이면 deny하고, absent gate 또는 다른 state는 silently allow한다. tool-call identity, payload content, audit/log/correlation metadata는 decision inputs가 아니다. audit/log/correlation metadata는 decision path 밖에 둔다.

## Dynamic Focus Contract

Dynamic focus control은 session gate contract의 일부다. work는 semantic atoms로 나뉘지만 attention scope는 고정되지 않고 한 방향으로만 expand하지 않는다. user interaction, mismatch location, verification burden, recovery cost는 current focus를 micro, meso, macro, meta layers 사이에서 이동시킬 수 있다.

- micro: tool call, command result, format check, single semantic atom
- meso: sub-task, boundary-contract surface, local source-target mapping
- macro: integrated output, SSOT alignment, user constraint alignment, cross-document logic
- meta: task necessity, task definition, scope expansion, premise validity

mismatch가 나타나면 runtime procedure는 cause를 포함하는 가장 작은 layer를 reopen한다. larger premise 또는 integrated logic이 틀렸으면 macro 또는 meta를 repair한다. atomic output 또는 local sub-task가 틀렸으면 micro 또는 meso를 repair한다. `calls`는 static and sparse 상태로 남으며 repeated focus movement, scope reopen point handling, re-verification loops는 procedure와 runtime verification에 속한다.

## Work-Impact Projection Contract

Work-Impact Projection은 hook-internal value를 다음 work decision을 바꾸는지 기준으로 분류한다. value는 work boundary, focus layer, verification burden, recovery path를 바꿀 수 있을 때 중요하다.

- hook execution과 strict audit log는 줄이지 않는다.
- `agent_visibility.profile`은 user-screen verbosity를 선택한다. hook execution, strict logging, work-impact classification을 gate하지 않는다.
- Forced/risk/gate values와 failed verification은 항상 user surface forced, model hint full로 break through한다.
- Routine/debug values는 strict log에 full로 남지만 focus, boundary, verification, recovery를 바꾸지 않으면 model hints에서 omit한다.
- Low-usefulness suspects, 예를 들어 duplicate reminders, clean-pass status, noop audit rows, debug counters, correlation ids, historical wording은 default로 `routine` 또는 `audit-only`다. active boundary, focus target, verification burden, recovery action을 바꿀 때만 promote한다.
- Unknown, ambiguous, failed values는 fuller surface로 fail closed하고 existing scope-reopen path를 통해 focus를 reopen한다.
- Goal: hook values는 task quality를 바꿀 수 있을 때만 focus, boundary, verification, recovery를 drive한다. Token reduction은 secondary consequence이며 success metric이 아니다.

## Runtime Checkpoints

Ghost-ALICE OS documents는 English canonical narrative + English control surface를 default coordination contract로 사용한다. reader-facing documentation tree가 pair를 expose하는 곳에는 Korean paired counterpart를 둔다. field names, enum values, literal tokens, gate schemas, allowed/forbidden values는 English로 유지하고 번역하지 않는다.

first commentary는 다음 block을 포함해야 한다.

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: session-intent-analyzer (this turn); task-router (this turn); using-coding-convention (this turn) | n/a
- next-required: <skill-name|none>
```

non-empty final response에는 completion claims, recommendations, choices, success judgments를 포함해 다음 block을 포함해야 한다.

```text
[completion-check]
- verification-before-completion: done
- skill-call: verification-before-completion (this turn)
- acceptance-criteria:
  - <criterion-id>: <user-intent-or-contract-condition> [source: user-explicit | inferred | previous-tool | system-doc]
- claim-evidence-map:
  - claim: <completion-or-recommendation-claim>
    criterion: <criterion-id>
    evidence: <fresh command, inspected file, source locator, or tool output>
    verdict: pass | fail
- unverified:
  - none
- evidence: <fresh command or inspected file>
```

`acceptance-criteria`는 user intent, locked decisions, boundary-contract에서 추출한 verifiable criteria다. `claim-evidence-map`은 final-response claim을 criterion과 fresh evidence에 연결한다. `unverified`가 `none`이 아니면 completion, success, recommendation이 settled된 것처럼 말하지 않는다. finalized `[completion-check]`는 `verdict: pass | fail`과 `unverified: none`을 사용한다. unverified item이 있으면 finalizing하지 말고 prose로 partial state와 remaining verification을 보고한다. Installed Stop completion hooks는 mandatory final-block mode로 동작한다. non-empty final response에 `[completion-check]`가 없으면 reject하고, empty transcript는 allow한다.

Hard sequence: skill load/call -> fresh verification -> [completion-check]. non-empty final response 전에는 current turn에서 `verification-before-completion`을 load/call하고, fresh verification을 run/read한 뒤에만 `skill-call: verification-before-completion (this turn)`가 있는 `[completion-check]`를 쓴다. If any step is missing or out of order, the completion-check is invalid.

`skill-call:` line은 그 skill workflow가 current turn에서 실제로 실행됐다는 record다. Claude Code에서는 visible Skill call 이후에만 쓴다. Codex처럼 visible Skill tool이 없는 환경에서는 current turn에 해당 skill의 `SKILL.md`를 실제로 읽고 workflow를 따른 뒤에만 쓴다.

`verification-before-completion`은 non-empty final response 직전 always-on lifecycle gate이며 completion claims, recommendations, choices, success judgments를 포함한다. Claude Code 같은 visible skill surface에서는 actual call 전 `[completion-check]`에 `skill-call: verification-before-completion (this turn)`를 쓰지 않는다. Codex에서는 해당 `SKILL.md`를 실제로 읽고 workflow를 따른 경우에만 쓴다.

`[completion-check]`가 `skill-call: verification-before-completion (this turn)`를 claim하면 같은 final response의 `[io-trace]` `skills-loaded`도 `verification-before-completion`을 포함해야 한다. Stop completion hook이 final response를 validate하는 곳에서는 이 mismatch 또는 missing `[completion-check]`가 retry loop를 만들 수 있다.

Codex environments without a visible Skill surface:

- required gate complete로 표시하기 전에 relevant `SKILL.md`를 읽는다.
- metadata, descriptions, memory, prior turns, "이미 안다"는 이유로 gate를 complete 처리하지 않는다.
- `SKILL.md`를 current turn에 읽지 않았다면 `skill-call:`에 쓰지 않는다. gate는 still pending이다.
- simple tasks, already-routed tasks, metadata가 충분해 보이는 cases에도 같은 기준을 적용한다.

## tool-checkpoint Visible Surface

`tool-checkpoint`는 PreToolUse/BeforeTool checkpoint다. user-input intake order의 일부가 아니며 `session-intent-analyzer`, `jailbreak-detector`, `task-router`보다 먼저 run한다고 설명하면 안 된다.

default `[tool-checkpoint]` block은 다음 decision fields를 요구한다. `intent`, `why`, `procedure`, `contract-ref`, `contract-check`, `localized-human-note`, `rejected-alternatives`, `unverified-premises`, `failure-mode-if-wrong`. 이 fields는 agent가 무엇을 하는지, action이 active boundary 안에 있는 이유, reject한 alternatives, 아직 검증되지 않은 premises, 판단이 틀렸을 때 실패하는 지점을 보여준다.

`recovery-action`은 conditional이다. failure mode가 concrete recovery step, scope reopen, external side effect handling, 또는 hard-to-recover action을 요구할 때만 추가한다. stable English action phrase 또는 slug로 유지한다. mismatch가 scope를 바꾸면 `procedure` 또는 `recovery-action`에 `focus-layer`와 `scope-reopen` target을 적는다.

routine tool checkpoint에서는 별도의 `recovery-cost` 또는 `recovery-note` fields를 요구하지 않는다. Recovery cost는 work-impact projection, verification planning, high-impact exception handling에 속하며, every PreToolUse message에 속하지 않는다. human-facing recovery explanation은 operator의 next decision을 바꿀 때만 `localized-human-note` 또는 conditional `recovery-action`에 둔다.

## tool-checkpoint Batch / Continuation Compression

`[tool-checkpoint:batch]`와 `[tool-checkpoint:continuation]`은 repeated full-gate cost를 줄이는 compact forms다. new permission을 만들지 않고 new tool action이 safe하다고 infer하지 않는다.

`[tool-checkpoint:continuation]`은 full `[tool-checkpoint]`로 이미 시작한 same process/session/tool-call id의 output polling만 가리킨다. new command, input, timeout, interruption, ref changes는 full `[tool-checkpoint]`로 돌아가야 한다.

same ref의 simple polling은 output을 영원히 반복하라는 의무가 아니다. compact form은 full gate 반복을 피한다. first poll 또는 state change 때 expose한다.

## Notes

- recommendations는 casual opinions가 아니라 verification이 필요한 claims로 취급한다.
- 같은 session에서 방금 inspect했더라도 fresh verification을 skip하지 않는다.
- each turn마다 `task-router`를 reapply한다.
- `task-router`는 `boundary-contract` 필요 여부만 결정한다. allowed-surface, file names, test-purpose는 `boundary-contract`가 소유한다.
- task-router가 `boundary-contract: required`를 output하면 next required gate는 boundary-contract다.
- `skill-call:` field는 form completion과 actual obligation completion이 섞이는 것을 구조적으로 막는다.
- metadata-only skill matching은 candidate discovery이지 execution이 아니다. Required gate skills는 actual `SKILL.md` file을 읽고 workflow를 따를 때만 complete다.
