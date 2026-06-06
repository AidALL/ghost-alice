# Tool Output Semantics

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/policies/tool-output-semantics.md)

This is the minimum contract for separating human-readable labels from machine-readable fields. It is not a license to create a broad new status taxonomy. Add only output surfaces tied to observed misdiagnosis cases.

## Contract

- Machine-readable fields are the control surface.
- Human labels are presentation only.
- Existing compatibility fields stay in place, but new consumers must not interpret compatibility fields as machine state.
- Add a new field only when it changes user action.

## Host Tool Input Schema Validation

Tool argument shape is owned by the host harness, not by Ghost-ALICE governance hooks. When a tool call omits a required field, the host validates the submitted arguments against that tool's JSON Schema before the tool body starts. A missing required array such as `questions` in an AskUserQuestion-style call is therefore a pre-execution validation failure: no user UI opens, no file or external side effect can occur, and recovery is to retry the same intent with schema-valid arguments.

Ghost-ALICE hooks must not duplicate this by inspecting arbitrary serialized `tool_input` content, deny-list substrings, or inferred payload capabilities. The governance boundary is session intent, downstream gate state, completion evidence, IO trace, and documented work contracts. Malformed tool payloads remain the host schema validator's responsibility.

## `_shared/install_hooks.py`

`check_status_detail(platform_key)` returns structured hook state. The existing `check_status(platform_key)` remains a compatibility wrapper and continues to return only `installed`, `missing`, `skipped`, or `unsupported`.

| field | meaning | compatibility |
| --- | --- | --- |
| `status_token` | machine-readable hook state | new |
| `status_label` | localized human label | presentation only |
| `legacy_status` | existing installer status string | compatibility only |
| `details` | missing or drifted hook labels and reason metadata | new |
| `missing_reason` | reason for `HOOK_MISSING` when known | new |
| `unsupported` | true when the platform runtime cannot run hooks | new |

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

`scan()` returns a JSON list of findings deduplicated by resolved path. `finding_count` remains a compatibility field, but new consumers must also inspect `finding_count_semantics`.

| field | meaning | compatibility |
| --- | --- | --- |
| `finding_count` | unique findings after resolved path dedupe | existing |
| `finding_count_semantics` | literal semantics of `finding_count` | new |
| `occurrence_count` | physical occurrence count including mirrors, if computed | optional |
