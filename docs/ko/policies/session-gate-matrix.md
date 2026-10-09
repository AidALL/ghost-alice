# Session Gate Matrix

언어: [🇺🇸 English](../../policies/session-gate-matrix.md) | 🇰🇷 한국어

session gate SSOT는 `skill-catalog/session-gates.json`입니다. 이 문서는 human-readable matrix입니다.
## Contents

- [Required Entrypoints](#required-entrypoints)
- [Turn Routing Contract](#turn-routing-contract)
- [Session Intent Ledger Contract](#session-intent-ledger-contract)
- [Runtime Hook Graph Contract](#runtime-hook-graph-contract)
- [Dynamic Focus Contract](#dynamic-focus-contract)
- [Routing Surface Contract](#routing-surface-contract)
- [Work-Impact Projection Contract](#work-impact-projection-contract)
- [Runtime Checkpoints](#runtime-checkpoints)
- [tool-checkpoint Visible Surface](#tool-checkpoint-visible-surface)
- [tool-checkpoint Batch / Continuation Compression](#tool-checkpoint-batch--continuation-compression)
- [Notes](#notes)


## Required Entrypoints

| Situation | Required entrypoint |
| --- | --- |
| 모든 user input의 first intake | `session-intent-analyzer` |
| 정확한 session-intent intake 이후, downstream work/tool calls 전 첫 agent routing step | `task-router` |
| development/coding request | `coding-convention/using-coding-convention` |
| bug fix | `coding-convention/systematic-debugging` |
| production code change | `coding-convention/test-driven-development` |
| executed work가 complete, fixed, successful, freshly verified라고 claim하는 final response | `coding-convention/verification-before-completion` |
| commit/push 직전 | `coding-convention/finishing-a-development-branch` |
| 새 task, sub-task, follow-up item 정의 시 | `necessity-gate` |
| task-router가 `boundary-contract: required`를 route한 직후 | `boundary-contract` |

## Turn Routing Contract

정상 경로는 `pending-merge precheck → session-intent-analyzer → task-router → downstream/tool-checkpoint`입니다. hook의 현재 pending-merge 결과를 먼저 사용하며, 결과가 없으면 실제 작업 전에 수동 확인을 마칩니다. bounded no-work terminal route만 이를 다음 실제 작업까지 미룰 수 있습니다.

session-intent intake는 task-router에 직접 전달됩니다. 같은 정확한 세션의 skill-evolution은 report-only terminal branch이며 task-router나 자동 생산 작업으로 연결되지 않습니다. 네이티브 실행 권한은 host가 소유하며, 감사 기록에서 권한을 만들지 않습니다.

task-router는 매 입력마다 현재 요청과 계속 승인된 작업을 대조하고 의미 분해, focus, skill assignment 및 현재 작업 선택을 소유합니다. intake, 원시 의도 추론, ledger update, credentials 또는 native permission은 소유하지 않습니다. 단순 질문이나 상태 질문도 routing 대상이지만 미완료 승인 작업을 자동으로 중단시키지 않습니다.

정확한 intake가 부족하면 해당 기록의 복구를 진행합니다. 다른 root, session, receipt 또는 과거 판단으로 대체하지 않습니다. 설치 소스와 hook 설정만으로 native firing을 입증하지 않으며 실제 smoke 절차는 [live-smoke-regression](live-smoke-regression.md)을 따릅니다.

## Session Intent Ledger Contract

hook은 raw prompt 대신 digest, 길이, 정확한 입력 식별자와 순서가 있는 audit event를 기록합니다. 의도, 제약, 결정 또는 완료 기준이 바뀔 때만 agent가 압축 semantic delta를 같은 receipt의 root/platform/session/input에 대해 공개 ledger API로 추가합니다.

현재 runtime authority는 receipt-bound ledger root의 `ghost-state.sqlite3`와 공개 SQLite ledger API입니다. `intent-state.json`, `intent-events.jsonl`, `current-session.json`은 호환성 import/export 또는 discovery 자료이며 현재 write identity나 runtime authority가 아닙니다. scalar는 최신 의미로 교정하고 목록은 stable identity로 병합합니다.

- Consumers: 정확한 session context를 소비하는 task-router와 report-only skill-evolution입니다.
- Forbidden: raw prompt, 전체 대화, tool output, system/developer instruction text, secret values입니다.
- digest-only observation은 intake 증거입니다. 의미 변화가 없으면 semantic delta를 만들지 않습니다.
- missing/degraded intake는 복구가 필요하다는 뜻이며 기존 human/native permission을 취소하지 않습니다.
- hookless 환경에서도 같은 identity/storage 계약을 지킵니다. 식별자를 추측하거나 대체 원장을 만들지 않습니다.

## Runtime Hook Graph Contract

1. hook pending-merge 결과를 먼저 처리합니다. known undecided entry는 merge-companion에 연결하며 사용자 명시적 defer/skip은 유효합니다.
2. session-intent hook은 digest-only intake와 audit를 원자적으로 기록합니다. 필요한 semantic delta는 같은 정확한 입력에만 연결합니다.
3. task-router는 exact intake를 소비해 현재 요청과 미완료 승인 작업을 대조한 뒤 현재 qualified execution 또는 nonexecution을 선택합니다. skill-evolution은 별도 report-only 종점입니다.
4. conditional boundary 이후 domain owner가 생산과 검증을 수행합니다. Core가 criterion evidence, completion/publication route와 실제 host delivery를 구분하고 AP는 현재 선택된 단위의 결과만 기록합니다.
5. `tool-checkpoint`는 `PreToolUse`/`BeforeTool` retry checkpoint입니다. 매 tool call의 strict audit를 유지하고 exact input lineage의 반복 surface만 줄입니다. tool-call identity, arbitrary payload 또는 audit metadata에서 native permission을 만들지 않습니다.

surface에는 `hook-stage: PreToolUse`와 `meaning: tool-call retry checkpoint, not user-input intake`를 함께 둡니다. native tool permission은 host에 남으며 human authority, credentials, source/owner/lineage 검증도 유지합니다.

## Dynamic Focus Contract

Dynamic focus control은 session gate contract의 일부입니다. work는 semantic atoms로 나뉘지만 attention scope는 고정되지 않고 한 방향으로만 expand하지 않습니다. user interaction, mismatch location, verification burden, recovery cost는 current focus를 micro, meso, macro, meta layers 사이에서 이동시킬 수 있습니다.

- micro: tool call, command result, format check, single semantic atom
- meso: sub-task, boundary-contract surface, local source-target mapping
- macro: integrated output, SSOT alignment, user constraint alignment, cross-document logic
- meta: task necessity, task definition, scope expansion, premise validity

mismatch가 나타나면 runtime procedure는 cause를 포함하는 가장 작은 layer를 reopen합니다. larger premise 또는 integrated logic이 틀렸으면 macro 또는 meta를 repair합니다. atomic output 또는 local sub-task가 틀렸으면 micro 또는 meso를 repair합니다. `calls`는 static and sparse 상태로 남습니다. A checkpoint runs only while a live uncertainty exists and its possible outcomes can change the next decision. Stop the sequence when a checkpoint produces no relevant state delta or when further checking displaces the primary objective.

## Routing Surface Contract

`task-router`는 current turn의 reusable work judgment를 소유하고, accepted session intent에서 `primary-request`, `causal-axis`, `response-order`를 보존합니다. `session-intent-analyzer`는 semantic facts와 accumulated decisions를 기록하며 competing routing scale을 만들지 않습니다.

The user's terminal objective outranks investigative means. investigation, provenance reconstruction, artifact preservation, worktree inspection은 user가 deliverable로 explicit하게 요청하지 않는 한 means입니다. Do not let a means replace, narrow, or expand the terminal objective.

`response-mode: clarification-only`는 current conversation에 essential referent 또는 decisive input이 없어 answer나 safe action을 시작할 수 없는 terminal route입니다. Intake and routing still run internally. Ask only for the minimum decisive information. context를 guess하기 위해 files, repositories, manifests, tools, credentials, external state를 inspect하지 않습니다. manual pending-merge check는 next actionable turn까지 defer합니다. Do not emit `[gate-state]`, `[tool-checkpoint]`, or `[io-trace]`; strict hook logging은 active 상태로 남습니다. the content already resolves the question이거나 user가 lookup/status check를 요청했거나 explicit assumption으로 bounded answer가 가능하면 이 route를 사용하지 않습니다.

`response-mode: direct-response`는 file change, side effect, current-state lookup, tool call, fresh verification 없이 current input과 conversation만으로 resolved content를 답할 수 있는 no-work terminal route입니다. `response-order: resolved-intent-first`를 사용합니다. Route classification precedes evidence planning. causal premise 자체는 inspection request가 아니고 verification burden은 current-state referent를 만들 수 없습니다. explicit inspection request 또는 conversation에서 이미 established된 referent만 local diagnosis를 허용합니다. An established referent authorizes inspection but does not require it. explicit correction 또는 non-goal을 먼저 accept하고, superseded means보다 terminal objective를 우선하며, current repository나 machine을 inspect하지 않고 general explanation 또는 stable low-risk how-to를 답합니다. Ambient working directory, opened project, and available tools are not user-provided referents or inspection authority. general why/how question에 나온 technical state는 active workspace에 대한 evidence가 아니라 explanation topic으로 취급합니다. Do not validate or rebut that premise before explaining. first-person, past-cause, deictic wording, tense, technical-state language, ambient context, tool availability은 question을 workspace에 bind하지 않으며, user가 workspace를 identify하거나 workspace evidence를 제공하거나 exact diagnosis/inspection을 명시적으로 요청할 때만 workspace가 target이 됩니다. Intake and routing still run internally; strict hook logging은 active입니다. missing manual pending-merge check를 defer하고 downstream skill을 load하지 않으며, decision-relevant caveat 최대 하나와 resolved content만 emit합니다. `[routing-surface]`, `[task-router]`, `[gate-state]`, `[tool-checkpoint]`, `[completion-check]`, `[io-trace]`를 emit하지 않습니다. current/version-specific fact, support/regression claim, high-risk advice, lookup, inspection, modification, verification request는 normal route를 사용합니다.

`routing-surface`의 `change-depth`, `focus-layer`, `verification-complexity`는 English canonical enum을 그대로 사용합니다. unknown, ambiguous, contradictory value는 fuller surface로 fail closed하고 existing scope reopen point를 통해 focus를 reopen합니다.

## Work-Impact Projection Contract

Work-Impact Projection은 hook-internal value를 다음 work decision을 바꾸는지 기준으로 분류합니다. value는 work boundary, focus layer, verification burden, recovery path를 바꿀 수 있을 때 중요합니다.

- hook execution과 strict audit log는 줄이지 않습니다.
- `agent_visibility.profile`은 user-screen verbosity를 선택합니다. hook execution, strict logging, work-impact classification을 gate하지 않습니다.
- Forced/risk/gate values와 failed verification은 항상 user surface forced, model hint full로 break through합니다.
- Routine/debug values는 strict log에 full로 남지만 focus, boundary, verification, recovery를 바꾸지 않으면 model hints에서 omit합니다.
- Low-usefulness suspects, 예를 들어 duplicate reminders, clean-pass status, noop audit rows, debug counters, correlation ids, historical wording은 default로 `routine` 또는 `audit-only`입니다. active boundary, focus target, verification burden, recovery action을 바꿀 때만 promote합니다.
- Unknown, ambiguous, failed values는 fuller surface로 fail closed하고 existing scope-reopen path를 통해 focus를 reopen합니다.
- Goal: hook values는 task quality를 바꿀 수 있을 때만 focus, boundary, verification, recovery를 drive합니다. Token reduction은 secondary consequence이며 success metric이 아닙니다.

## Runtime Checkpoints

Ghost-ALICE OS documents는 English canonical narrative + English control surface를 default coordination contract로 사용합니다. reader-facing documentation tree가 pair를 expose하는 곳에는 Korean paired counterpart를 둡니다. field names, enum values, literal tokens, gate schemas, allowed/forbidden values는 English로 유지하고 번역하지 않습니다.

Active work continuation: no-work terminal route를 선택하기 전에 current input과 미완료 승인 작업을 대조합니다. 진행 중인 작업의 상태 질문, 설명 요청, 교정에는 짧은 commentary로 답하고 지원되는 작업을 계속합니다. 명시적인 stop, pause, cancellation, incompatible replacement를 따릅니다. ambient context나 미해결 ledger flag만으로 작업 권한을 추론하지 않습니다. 고정된 branch-local limit는 그에 의존하는 작업만 막습니다. conduct feedback 기록이나 검증된 보고서는 요청된 구현의 완료 기준을 충족하지 않습니다. 사용자가 교정을 승인하면 해당 workflow에서 구현하고 원래 실패를 회귀 테스트합니다.

first commentary는 normal route에서 다음 block을 포함해야 하며 no-work terminal route는 control block을 emit하지 않습니다.

```text
[gate-state]
- merge-companion-precheck: clean | pending=N | unsupported
- session-intent-analyzer: done | hook-observed | pending
- task-router: done
- using-coding-convention: done | n/a
- boundary-contract: required | done | n/a
- skill-call: <each skill actually called this turn, as name (this turn)> | n/a
- next-required: <skill-name|none>
```

final response가 executed work의 complete, fixed, successful, freshly verified 상태를 claim할 때 다음 block을 포함해야 합니다.

```text
[completion-check]
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

`acceptance-criteria`는 user intent, locked decisions, boundary-contract에서 추출한 verifiable criteria입니다. `claim-evidence-map`은 closure claim을 criterion과 fresh evidence에 연결합니다. `unverified`가 `none`이 아니면 completion 또는 success가 settled된 것처럼 말하지 않습니다. finalized `[completion-check]`는 `verdict: pass | fail`과 `unverified: none`을 사용합니다. unverified item이 있으면 finalizing하지 말고 prose로 partial state와 remaining verification을 보고합니다. Installed Stop completion hooks는 executed-work closure claim에 `[completion-check]`를 요구하고 routine non-closure response는 allow합니다.

Every user input reopens routing; it does not by itself invalidate unchanged evidence or require reverification. Explaining unchanged prior work is not a new closure claim. Reverify when the relevant state, artifact, or criterion changed; a new error, mismatch, contradiction, or instability appeared; or the user explicitly requested a new check. A change is an observed mutation event, not a possibility, a storage location, or a new user message.

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources. Verification output alone does not create a new obligation to verify the verification.

Before re-reading an artifact to support a claim, apply verify-or-reuse: reuse retained evidence unless an observed trigger exists. Answer an objection that a check was redundant from the recorded mutation authority and events, not from hypothetical external changes. Say each fact, status, plan, and apology once; later messages carry only new results or decisions. canonical contract는 `coding-convention/verification-before-completion/references/verify-or-reuse.md`이며 Claude와 Codex가 같은 contract를 그대로 쓰고, platform adapter는 tool name mapping만 담당합니다.

When you confirm a flaw in a prompt or other deliverable you provided, return the corrected complete deliverable unless the user asked only for diagnosis (`confirmed-flaw-not-propagated`).

Hard sequence for a new current-turn closure claim: skill load/call -> decision-relevant fresh verification -> [completion-check]. A successful result already returned in this turn, such as a write or a test run, is that fresh verification; the order places the skill before the claim and does not require repeating a successful check. 해당 claim 전에 current turn에서 `verification-before-completion`을 load/call하고, decision-relevant fresh verification을 run/read한 뒤에만 `[completion-check]`를 씁니다. If any step is missing or out of order, the completion-check is invalid.

`skill-call:` line은 그 skill workflow가 current turn에서 실제로 실행됐다는 record입니다. Claude Code에서는 visible Skill call 이후에만 씁니다. Codex처럼 visible Skill tool이 없는 환경에서는 새로 읽거나 유효하게 보존된 instruction body를 사용하여 current turn의 workflow를 따른 뒤에만 씁니다.

`verification-before-completion`은 executed-work closure claim 전 lifecycle gate입니다. Routine explanations, meta-discussion, options는 finished work 또는 verified result를 claim하지 않는 한 이 gate를 요구하지 않습니다. Claude Code에서는 `[completion-check]`에 `skill-call: verification-before-completion (this turn)` 줄이 없습니다. Stop hook이 visible Skill call을 transcript에서 확인합니다. Codex에서는 새로 읽거나 유효하게 보존된 instruction body로 해당 workflow를 따른 경우에만 그 줄을 씁니다.

화면의 `[io-trace]`는 선택 사항이며 strict audit과 실제 현재 턴 검증은 유지합니다. Claude의 실제 Skill 호출과 Codex의 실제 body 사용 및 workflow 근거를 정직하게 기록합니다. Core는 사용 가능한 이전 업무 본문이 실제로 보이고 claims가 같을 때만 metadata-only repair를 선택합니다. final-only, unknown 또는 usable prior payload가 없으면 하나의 완전한 답변을 조립하고, claims가 바뀌면 교정된 완전한 답변을 제공합니다. 근거가 없는 주장은 철회하거나 partial로 보고합니다. 기록이나 형식을 고치기 위해 변경 없는 업무와 검증을 반복하지 않습니다. host는 업무 본문을 ledger 밖에 보존합니다.

Codex environments without a visible Skill surface:

- required gate complete로 표시하기 전에 relevant instruction body를 새로 읽거나 유효하게 재사용합니다.
- metadata, descriptions, prior verdict, "이미 안다"는 이유로 gate를 complete 처리하지 않습니다.
- 새로 읽은 body도 유효하게 보존된 body도 없으면 gate는 pending입니다. `skill-call:`에 쓰지 않습니다.
- simple tasks, already-routed tasks, metadata가 충분해 보이는 cases에도 같은 기준을 적용합니다.

## tool-checkpoint Visible Surface

`tool-checkpoint`는 PreToolUse/BeforeTool checkpoint입니다. user-input intake order의 일부가 아니며 `session-intent-analyzer`, `task-router`보다 먼저 run한다고 설명하면 안 됩니다.

default `[tool-checkpoint]` block은 `intent`와 `why`를 carries합니다. `procedure`는 next work decision을 바꾸거나 non-routine step을 명확히 할 때 추가합니다. `contract-ref`와 `contract-check`는 boundary-contract가 active일 때 추가합니다. `localized-human-note`, `rejected-alternatives`, `unverified-premises`, `failure-mode-if-wrong`은 side effect, forced signal, mismatch, meaningful user decision point 때문에 field가 useful할 때만 추가합니다. 이렇게 checkpoint procedure는 유지하되 work boundary, focus layer, verification burden, recovery를 바꾸지 않는 low-impact tool call에 무의미한 값을 반복하지 않습니다. Add `verify-or-reuse` when a call re-reads an artifact to support a claim, with `why` naming the observed trigger. 이 field는 model-side reasoning입니다. hook은 retry와 strict audit를 소유하며 native permission을 대신하지 않습니다.

`recovery-action`은 conditional입니다. failure mode가 concrete recovery step, scope reopen, external side effect handling, 또는 hard-to-recover action을 요구할 때만 추가합니다. stable English action phrase 또는 slug로 유지합니다. mismatch가 scope를 바꾸면 `procedure` 또는 `recovery-action`에 `focus-layer`와 `scope-reopen` target을 적습니다.

routine tool checkpoint에서는 별도의 `recovery-cost` 또는 `recovery-note` fields를 요구하지 않습니다. Recovery cost는 work-impact projection, verification planning, high-impact exception handling에 속하며, every PreToolUse message에 속하지 않습니다. human-facing recovery explanation은 operator의 next decision을 바꿀 때만 `localized-human-note` 또는 conditional `recovery-action`에 둡니다.

## tool-checkpoint Batch / Continuation Compression

Runtime hook은 session input lineage마다 full `[tool-checkpoint]`를 한 번 surface하고, 같은 lineage의 later tool call은 계속 check하되 user-facing text는 반복하지 않습니다.

`[tool-checkpoint:batch]`와 `[tool-checkpoint:continuation]`은 repeated full-gate cost를 줄이는 compact forms입니다. new permission을 만들지 않고 new tool action이 safe하다고 infer하지 않습니다.

`[tool-checkpoint:continuation]`은 same process/session/tool-call id의 output polling만 가리킵니다. new user input, 현재 상태의 실패 또는 authority 변화, mismatch, other state change는 surfaced `[tool-checkpoint]`로 돌아갑니다.

same ref의 simple polling은 output을 영원히 반복하라는 의무가 아닙니다. compact form은 full gate 반복을 피합니다. input lineage의 first poll 또는 state change 때 expose합니다.

## Notes

- recommendations는 casual opinions가 아니라 verification이 필요한 claims로 취급합니다.
- relevant state와 criterion이 unchanged이면 existing evidence를 reuse하고, 그 age를 밝히되 current-turn verification으로 relabel하지 않습니다.
- each turn마다 `task-router`를 reapply합니다.
- `task-router`는 `boundary-contract` 필요 여부만 결정합니다. allowed-surface, file names, test-purpose는 `boundary-contract`가 소유합니다.
- task-router가 `boundary-contract: required`를 output하면 next required gate는 boundary-contract입니다.
- `skill-call:` field는 form completion과 actual obligation completion이 섞이는 것을 구조적으로 막습니다.
- metadata-only skill matching은 candidate discovery이지 execution이 아닙니다. Required gate skills는 fresh 또는 valid retained instruction body를 사용하고 현재 입력의 workflow를 따를 때만 complete입니다.

Instruction body 재사용은 canonical `coding-convention/verification-before-completion/references/verify-or-reuse.md`의 Instruction Body Reuse를 따릅니다. 동일 세션의 코드 기록과 실제로 보존된 body가 필요합니다. 현재 입력의 의미 판단과 gate workflow는 계속 수행합니다. 실제 읽기와 유효한 재사용은 내부 실행 근거에 구분하여 남깁니다. 화면 추적 목록은 요청되거나 판단에 필요할 때만 표시합니다.

## Managed Completion Recording Contract

Core는 accepted response route와 owner, work-unit ID 및 contract revision을 기준으로 current-ready-owned 실행 단위만 선택합니다. AP v2는 해당 단위의 admitted 기준을 기록하며 누적 이력과 unadmitted 보호 조건을 실행 의무로 바꾸지 않습니다. 관리되는 no-argument 훅 경로는 `autopilot-completion-receipt.v2`를 선택하고 직접 호출하는 공개 API의 기본값은 v1입니다. 위치와 protocol 전달은 session, origin, native trust, admission 또는 approval을 부여하지 않습니다.

새 publication request는 호환되는 기존 근거의 원본 source와 검증 시간을 유지한 채 현재 입력에 연결할 수 있습니다. 새 입력 자체는 변경되지 않은 업무 검증을 다시 열지 않습니다. 적용 여부는 Core, 기록 가능 여부는 AP가 소유합니다. recording-only 실패 시 intake와 binding 복구 도구는 계속 사용할 수 있으며 기록을 복구할 때까지 publication과 application을 차단합니다. 독립적으로 승인된 업무는 계속 진행하고 실제 기준, 관련 산출물 또는 owner/target이 바뀐 검증만 다시 수행합니다. 메타데이터만 고치는 응답은 사용 가능한 이전 답변이 실제로 보이고 claims가 같을 때만 허용됩니다. 그 외에는 완전한 답변을 조립하고 근거가 없으면 지원되지 않는 완료 주장을 정직하게 수정합니다. 출력 io-trace는 선택이며 strict audit은 유지합니다.

## Current Selection and Bounded Recovery

Core는 현재 요청과 계속 승인된 작업을 대조한 뒤 execution/nonexecution을 선택합니다. 현재 선택된 실행이 없는 bounded discussion은 AP publication을 하지 않습니다. 실제 실행한 read-only review/report나 positive closure는 owning verification을 따릅니다. Core가 routing projection과 `response-route.v1`을 소유하며 selector는 domain owner의 criterion check를 면제하지 못합니다.

AP가 enabled이면 실제 current-ready qualified selected execution과 Core `publication_route: none | prepare | publish | blocked`를 소비합니다. run 존재나 누적 backlog만으로 publication을 시작하지 않습니다. missing/stale route는 종속 capture/advancement만 막고 기존 human/native permission을 바꾸지 않습니다. 새 입력은 request identity를 다시 연결하며 변경 없는 valid proof의 원본 source/time은 유지합니다. criterion, artifact 또는 dependency의 실제 변화가 있을 때 영향받은 proof만 다시 엽니다. criterion별 evidence 결정, aggregate publication, host delivery는 별개입니다.

첫 recording-only failure는 bounded repair를 허용합니다. 동일한 실패가 변경 없이 반복되면 decision-relevant recording/dependency 변화까지 기록을 보류합니다. 독립적으로 승인된 작업은 계속하며 root, source, receipt, approval 또는 target을 추측하지 않습니다. supported business outcome, recording/application, whole-unit completion, delivery scope는 구별합니다.

owner는 같은 승인 작업 안에서 artifact, mapping, composition, route 또는 inferred premise를 다시 열고 생산 작업으로 돌아갈 수 있습니다. micro/meso/macro/meta는 양방향 focus이며 순서대로 모두 올라가는 의식이 아닙니다. 정확한 human-owned decision이나 새로운 explicit authority가 필요할 때만 사용자에게 묻습니다. evolution은 report-only 종점입니다.

## Repair and authority conditions

현재 입력과 이전의 실제 사용자 지시가 목표·금지 사항·허용 행동과 대상·부수 효과·자원에 관한 권한의 출처입니다. 보관된 권한 사실과 신뢰된 system/project 제약에는 각각 출처를 유지합니다. intake와 router가 권한을 부여하지는 않습니다. 사용자가 정한 기준과 자원 한도는 에이전트가 도출한 해석이나 한정된 실행 계획과 구별합니다. 명시적 자원 한도가 없다는 이유로 승인된 한도를 만들지 않습니다. 실제 권한 안에서 구현 계획을 갱신하되 수락 기준을 약화하지 않습니다. 경계를 넘기 전에 정확한 사람 소유 결정을 요청합니다. 실제 승인만 해당 계획과 권한을 갱신하며, 거부는 해당 경로를 보류하고 응답이 없으면 대기를 유지합니다.

실패한 복구 라운드는 수정과 완료된 해당 관측에서 같은 미해결 실패가 확인된 경우입니다. 최초 실패는 0회입니다. 미해결 실패·기준·단계·대상으로 사건을 구별하고 각 결과가 원인·매핑·설계·검증 판단에 무엇을 바꾸는지 설명합니다. 담당자나 세션이 바뀌어도 누적 시도와 최초 다섯 회 재평가의 처리 여부를 유지합니다. 원인을 모른다는 이유로 이름이나 횟수를 초기화하지 않으며, 같은 오류 문구만으로 서로 다른 업무·기록 실패를 합치지 않습니다. 해결이 입증된 뒤 재발하면 연결된 새 사건으로 다룹니다. 다섯 회는 최초 필수 전제 재평가 전의 최대 실패 횟수이며 최소 재시도 횟수나 전체 작업 한도가 아닙니다. 이 재평가를 처리했다면 누적 여섯·일곱 회라는 사실만으로 다시 발동하지 않습니다. 새로운 관련 반대 근거나 현재 중단 조건은 별도로 재평가를 요구할 수 있습니다. 계속하려면 구별 가능한 가설·검사·명시적인 추가 시도 또는 자원 한도·중단 조건이 남은 전체 자원 안에 있어야 합니다. 재평가는 횟수나 자원을 초기화하지 않으며 한도가 소진되면 해당 진행을 보류합니다. 다섯 회만으로 사람에게 결정을 요구하지 않습니다.

재량 검사는 미해결 기준이나 경쟁 설명, 검사가 이를 구별하는 이유, 서로 다른 구체적 다음 행동·수락 판단·경계 결정으로 이어지는 결과를 명시합니다. 무언가 발견할 가능성만으로는 부족합니다. 확률적 반복에는 불확실성·표본 추출·중단 규칙이 필요합니다. 필수 검사는 유지하며 필수 근거가 없거나 기준이 미해결이면 완료할 수 없습니다. 변하지 않은 유효한 proof는 원래 출처와 시간으로 재사용합니다. 이미 실행한 업무의 상태가 불명확하면 해당 관측을 복구하며 업무를 자동 재실행하지 않습니다. 아직 실행하지 않은 필수 업무는 실제 실행이 필요합니다. 불확정 검사는 업무 성공·실패나 복구 실패 횟수를 확정하지 않지만 관측·기록 시도도 한정된 자원을 소비합니다.

기존 작업 문맥과 인계에 판단을 유지하며 별도 counter hook·classifier·매 대화 기록·필수 보고서를 만들지 않습니다. 변하지 않은 동일 기록 실패는 담당 typed recovery에 따라 다섯 회를 기다리지 않고 보류합니다. 기록만 실패했다면 업무 proof를 유지합니다. 보류·사람 응답 대기 경로는 실제 재개 조건 전까지 선택에서 제외합니다. 선택할 업무가 없으면 모든 필수 기준의 유효한 proof와 관련 반대 근거를 확인하여 담당 완료 검증으로 진행하며, 충족하지 못하면 정직한 부분·대기 상태를 반환합니다.

담당 workflow는 선택한 업무나 수정을 호출하고 실제 결과를 받으며, 선언한 실행 결과 자체가 검증 관측이 될 수 있습니다. 도메인 검증은 실제 관측·출처·시간을 workflow에 반환하여 기준을 판단하게 합니다. 적용되는 등록 v2 pretool은 도구 실행 전에 AP origin·receipt를 자동 준비하고 현재 qualified 명령 문맥을 반환합니다. 유효한 준비 결과를 재사용하며 적용되는 의존성이 실제 미충족일 때만 반환된 명시적 준비 helper를 사용합니다. 기록 누락은 capture·publication·application의 기록 가능성에 영향을 주며 AP pretool은 계속 진행하고 native 도구 권한은 host가 담당합니다. 필수 또는 판단을 구별하는 업무 관측은 자체 권한·자원 한도를 따르며 기록 복구 규칙은 기록 시도에만 적용됩니다. 기록이 불가능한 경우에도 유효한 proof와 원래 출처·시간을 유지합니다.

후보 완료를 위해 담당 workflow가 근거를 얻거나 유효한 proof를 재사용합니다. 적용되는 AP publication은 정확한 현재 qualified 선택·prepared AP receipt·실제 반환된 publish 명령으로 pending-adapter 요청을 만듭니다. Native candidate/final dispatch는 실제 등록된 handler를 호출합니다. 완료 guard는 후보의 필드와 해당 skill 근거를 확인하고 privileged AP Stop adapter는 publication·proof·소유권·의존성을 재검증합니다. 현재 유효한 publication만 공유 권한 transaction에서 정확한 Core 기준 writer로 진행합니다. 누락·오래됨·무효 상태는 기준 기록 없이 기록 진단을 반환합니다. 실제 application 상태와 남은 기준이 단위별 기록·완료 주장의 근거입니다. Handler는 실제 native 호출 규칙을 따릅니다. Core는 전달 정책과 허용된 payload 조합을 담당하며 workflow/native host가 실제 화면에 전달합니다. 후보 주장은 현재 지원되는 범위를 사용하고 실제 기록·적용·관측된 전달 결과가 후속 복구와 완료 주장을 결정합니다. 이미 보이는 변경 없는 업무 본문은 해당 control-only repair로 보존합니다. 가능한 후속 조치는 workflow로 반환하고 선택 가능한 진행이 없으면 정직한 부분·대기 상태를 반환합니다.
