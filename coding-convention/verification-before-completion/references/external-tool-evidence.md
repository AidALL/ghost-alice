# External Tool Web-Search-First Gate

Layer marker: `web-search-first`.

If the final claim includes factual behavior about an external tool, library, CLI, SDK, framework, version, or platform behavior, apply the web-search evidence gate before the claim.

Categories:

- Category A, specification definition: one official source may be enough when the claim is only what the spec says should happen.
- Category B, runtime behavior: run at least three WebSearch queries.
- Category C, version-dependent behavior: run at least three WebSearch queries, including the version or year.

Minimum query pattern for Category B or C:

- `<tool> <year> github issue`
- `<tool> reddit`
- `<tool> not working <version>`

Evidence block extension:

```text
- web-search-evidence:
  - query: <query 1>
    accessible_url: <url>
    finding: <key finding or value>
    source-locator:
      source_type: web
      region: n/a
  - query: <query 2>
    accessible_url: <url>
    finding: <key finding or value>
    source-locator:
      source_type: web
      region: n/a
  - query: <query 3>
    accessible_url: <url>
    finding: <key finding or value>
    source-locator:
      source_type: web
      region: n/a
```

Source-locator contract:

- Web evidence must include `accessible_url`.
- Attached or local file evidence must include `file_path`, `page`, and `region`.
- `region` values are `top`, `middle`, `bottom`, or `n/a`. Literal enum form: `top | middle | bottom | n/a`.
- Materials without pages use `page: n/a` plus an equivalent locator such as section, row, slide, or sheet in `locator_note`.
- Numeric claims, original sources, tables, and figures must bind the specific value to its source location.

When Category B or C appears and `web-search-evidence` has fewer than three entries, lacks `accessible_url`, or lacks `source-locator`, the completion claim is invalid. Search again, fill the evidence, then claim only what the evidence supports.

This gate exists because official docs describe intended behavior, while community reports often reveal runtime regressions, race conditions, and version-dependent failures.

The only exception is an explicit user instruction for this session to waive web-search evidence.
