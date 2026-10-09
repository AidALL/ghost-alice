# Tool Output Semantics

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/tool-output-semantics.md)

This is the minimum contract for separating human-readable labels from machine-readable fields. It is not a license to create a broad new status taxonomy. Add only output surfaces tied to observed misdiagnosis cases.

## Contract

- Machine-readable fields are the control surface.
- Human labels are presentation only.
- Consumers must not interpret compatibility fields as machine state.
- Add a new field only when it changes user action.

## Host Tool Input Schema Validation

Tool argument shape is owned by the host harness, not by Ghost-ALICE governance hooks. When a tool call omits a required field, the host validates the submitted arguments against that tool's JSON Schema before the tool body starts. A missing required array such as `questions` in an AskUserQuestion-style call is therefore a pre-execution validation failure: no user UI opens, no file or external side effect can occur, and recovery is to retry the same intent with schema-valid arguments.

Ghost-ALICE hooks must not duplicate this by inspecting arbitrary serialized `tool_input` content, deny-list substrings, or inferred payload capabilities. The governance boundary is session intent, current work routing, completion evidence, strict audit, and documented work contracts. Malformed tool payloads remain the host schema validator's responsibility.

## `_shared/install_hooks.py`

`check_status_detail(platform_key)` returns structured hook state. `check_status(platform_key)` is a compatibility wrapper returning `installed`, `missing`, `skipped`, or `unsupported`.

| field | meaning | role |
| --- | --- | --- |
| `status_token` | machine-readable hook state | structured state |
| `status_label` | localized human label | presentation only |
| `legacy_status` | installer status string | compatibility only |
| `details` | missing or drifted hook labels and reason metadata | structured state |
| `missing_reason` | reason for `HOOK_MISSING` when known | structured state |
| `unsupported` | true when the platform runtime cannot run hooks | structured state |

Hook status tokens:

| status_token | trigger | user action |
| --- | --- | --- |
| `HOOK_INSTALLED_OK` | required marker and expected command all match | none |
| `HOOK_INSTALLED_DRIFT` | required marker exists but expected command differs | reinstall hooks or inspect local hook edits |
| `HOOK_CONFIG_DISABLED` | hook config exists but runtime feature flag/config disables hooks | enable hook config |
| `HOOK_MISSING` | hook config file or required marker is absent | install hooks or restore missing marker |
| `HOOK_UNSUPPORTED` | platform runtime cannot run hooks | use hookless fallback |
| `HOOK_PLATFORM_MISSING` | platform config directory is absent | skip platform or install that platform |

`HOOK_MISSING` uses `missing_reason`:

| missing_reason | trigger |
| --- | --- |
| `config_file_absent` | hook config file does not exist |
| `suite_incomplete` | hook config exists but at least one required marker is absent |

`HOOK_INSTALLED_DRIFT` is not `no hooks`. It means a Ghost-ALICE marker is present but the installed command no longer matches the current installer payload.

## `agent-security-scan/scripts/scan_agent_security_surface.py`

`scan()` returns a JSON list of findings deduplicated by resolved path. Consumers of `finding_count` also inspect `finding_count_semantics`.

| field | meaning | role |
| --- | --- | --- |
| `finding_count` | unique findings after resolved path dedupe | count |
| `finding_count_semantics` | literal semantics of `finding_count` | count interpretation |
| `occurrence_count` | physical occurrence count including mirrors, if computed | optional |

## Completion, Recording and Delivery

Core's `completion-route.v1` separates `evidence_route` from `publication_route` (`none | prepare | publish | blocked`). Per-criterion `check_ids` identifies affected owner work; `required_check_ids` preserves the full owner plan. Aggregate priority is a summary, not a command to rerun every check. AP uses the actual current ready qualified selection, never historical backlog or run existence alone.

A supported business criterion, a prepared/published request, an applied request and a complete work unit are distinct. Missing proof is unavailable, not pass. Original `proof_ref` and `verified_at` survive unchanged reuse across new input bindings. The first recording-only failure allows bounded repair; identical unchanged failure parks until relevant recording/dependency change. Existing human/native permission and independent authorized work remain intact.

Core `response_delivery.py` returns `preserve_answer_and_patch_metadata` only for usable visible prior payload and unchanged claims; `assembled_complete_payload` covers final-only/unknown/missing prior payload; changed claims use `corrected_complete_answer`. These selectors neither verify domain truth nor execute permissions. Unsupported claims must be withdrawn or reported partial. JSON exports and printed io-trace cannot substitute for runtime authority or completion evidence.

## Repair and check decisions

Actual current and prior human instructions supply the authorization envelope: objective, prohibitions, allowed actions and targets, side effects and resources. Retained authority facts preserve their human source; trusted system/project constraints retain their identified source. Intake compresses semantic facts and the router selects current work without granting authority. Distinguish human-owned criteria and resource choices from agent-derived interpretations and bounded local plans; an absent explicit resource cap is not an approved human limit. Renew a narrower implementation contract only inside actual authority, without weakening acceptance criteria or inferring permission from focus expansion. Request an exact human-owned decision before crossing its boundary. Actual grant renews only the affected plan and granted scope; denial parks it; no response leaves it waiting. Continue other eligible authorized work.

Before a discretionary check, name the unresolved criterion or competing explanation, why the method discriminates, and attainable outcomes mapped to different specified actions, acceptance judgments or boundary decisions. Mere potential to find something is insufficient. Stochastic repeats require uncertainty, sampling and stopping rules. Mandatory owning checks remain required; unavailable proof or unresolved required criteria prevent completion. Reuse valid unchanged passing proof at its original source and time; reopen only affected proof after observed change, contradiction or explicit recheck. Unknown already-executed business state calls for bounded affected observation, not automatic business replay; not-yet-executed required work still needs its actual action. Inconclusive checks give no business pass/fail verdict or failed-business-repair increment, but observation and recording attempts consume their bounded resources.

A failed repair round requires a corrective change and a completed relevant observation of the same unresolved failure. Initial failure is zero. Group by unresolved failure, affected criterion, stage and target; explain what each result changes about cause, mapping, design or verification. Unknown causes cannot justify relabeling/reset, and identical error text cannot combine unrelated business and recording failures. Preserve cumulative attempts and the addressed first-five checkpoint across agents and handoffs. Demonstrated resolution followed by recurrence is a linked new incident. Five failed rounds is the maximum before the first mandatory premise reassessment, not a minimum retry count or whole-task limit. Once addressed, cumulative six or seven alone do not retrigger that checkpoint. New relevant contradiction or a current stopping condition can require reassessment. Continuation requires a distinguishable hypothesis, a discriminating check, an explicit additional-attempt or resource allowance and a stopping condition inside remaining parent resources. Reassessment resets neither attempts nor resources; exhaustion parks the affected continuation. Five alone does not require human escalation.

Retain decisions in existing work context and handoffs; no counter hook, classifier, compulsory per-conversation record or new mandatory report is required. Recording-specific typed recovery parks an identical unchanged failure without waiting for five rounds. Preserve known business proof when only recording fails. Exclude parked and human-waiting branches until an actual relevant reopening event. If no eligible work remains, use valid proof for all required criteria and resolve relevant contrary evidence before owning completion verification; otherwise report honest partial/waiting status.

The owning workflow calls the selected business action or corrective change and receives its actual result; declared action output may itself supply the verification observation. Domain verification returns actual observations, source and time to the workflow for criterion judgment. The applicable registered v2 pretool automatically prepares AP origin/receipt before the tool and returns current qualified command context. Reuse that valid prepared result; use an explicit returned preparation helper only when the applicable dependency remains genuinely unfulfilled. A recording gap affects capture/publication/application recordability; AP pretool continues and native tool permission remains with its host. Required or discriminating business observation follows its own authority and resource bounds; recording recovery rules govern recording attempts only. Preserve valid proof and its original source/time, including when recording is unavailable.

For a candidate closure, the owning workflow obtains or reuses supported proof. Applicable AP publication uses the exact current qualified selection, prepared AP receipt and actual returned publish command to leave a pending-adapter request. Native candidate/final dispatch invokes its actually registered handlers: the completion guard checks candidate fields and applicable skill evidence; the privileged AP Stop adapter revalidates publication, proof, ownership and dependencies. Only accepted current publication reaches the exact Core criterion writer within the shared authority transaction. Missing, stale or invalid publication returns a recording diagnostic without a criterion write. Actual application status and remaining criteria determine unit recording/completion claims. The handlers retain their actual native invocation semantics. Core owns delivery policy and may assemble the permitted payload; the workflow/native host performs visible delivery. Candidate claims use the supported current scope, while actual recording/application and visibility results govern further repair and closure claims. Preserve already-visible unchanged business content for applicable control-only repair. Return an admissible affected step to the workflow, or honest partial/waiting status when no eligible continuation remains.
