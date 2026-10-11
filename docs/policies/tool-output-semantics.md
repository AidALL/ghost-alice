# Tool Output Semantics

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/tool-output-semantics.md)

This is the minimum contract for separating human-readable labels from machine-readable fields. It is not a license to create a broad new status taxonomy. Add only output surfaces tied to observed misdiagnosis cases.

## Contract

- Machine-readable fields are the control surface.
- Human labels are presentation only.
- Consumers must not interpret compatibility fields as machine state.
- Add a new field only when it changes user action.

## Host Tool Input Schema Validation

The host harness owns tool argument validation. Ghost-ALICE governance hooks do not duplicate the host's tool schema. For a host-reported argument validation failure, use the actual error to correct the submitted arguments before retrying the authorized action.

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

The responding model makes two judgments: each claim against its original sources, then the complete answer against the user's objective and constraints. Core authenticates the recorded judgments. The [architecture](../../architecture.md) maps the model, host and code handoffs; the [review and publication procedure](../../coding-convention/verification-before-completion/references/autopilot-publication.md) owns the executable calls and recording recovery.

| Output | Producer and consumer | Meaning |
| --- | --- | --- |
| `status=reviewed`, `response_text` | Core review API → host tool result → responding model | Required judgments support the exact answer, which the model may submit. It can accurately report incomplete work. |
| `status=unsupported` or a named prerequisite error | Core review API → host tool result → responding model | No supported answer is returned for submission. The model follows the owning procedure for affected correction or unresolved reporting. |
| `evidence_route`, `publication_route` | Core `completion-route.v1` → native completion handlers | Evidence selection is separate from publication state (`none | prepare | publish | blocked`). |
| `check_ids`, `required_check_ids` | Core completion route → affected-work consumer | The first names affected owner checks; the second preserves the full owner plan. Aggregate priority does not request replay of every check. |
| `proof_ref`, `verified_at` | Original reviewed proof → Ghost-ALICE Autopilot recording and application | Retain the original reference and verification time during unchanged reuse. |
| `pending-adapter`, applied result | Ghost-ALICE Autopilot adapter → native host and recorded application consumer | Accepted recording and guarded criterion application are different stages. Missing or unsupported proof cannot mark a criterion met. |
| Stop status and optional task request | Independently invoked Core guard / Ghost-ALICE Autopilot adapter → host | The guard returns allow or a named rejection. Ghost-ALICE Autopilot selects only from the admitted plan. A returned task proves neither host delivery nor business execution. |

Core `response_delivery.py` selects the response shape from supplied host context:

| Selector | Condition |
| --- | --- |
| `preserve_answer_and_patch_metadata` | A usable prior answer is visible and its claims are unchanged. |
| `assembled_complete_payload` | Delivery is final-only or there is no usable prior answer. |
| `resolve_host_delivery` | The model must resolve unknown visibility from supplied conversation/host context. |
| `corrected_complete_answer` | The answer's claims changed. |

These selectors establish neither domain truth nor permission. The [Apply and deliver procedure](../../coding-convention/verification-before-completion/references/autopilot-publication.md#apply-and-deliver) defines the model's next action, including preserving valid proof and visible unchanged content. The [session gate owner map](session-gate-matrix.md#repair-and-authority-conditions) identifies authority, repair and useful-check procedures.
