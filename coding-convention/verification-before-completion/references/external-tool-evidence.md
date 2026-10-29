# External Tool Evidence

The current-session assistant applies this reference when endorsing a material factual claim about an external tool, library, CLI, SDK, framework, version or platform, or when the user explicitly requests verification of such a claim. This reference owns the evidence decision; user-input hooks do not select searches. Follow the main skill's evidence selection and stop gate before another observation.

## Select evidence for the claim

Identify the criterion, claimed behavior and unresolved uncertainty from the actual task. Stable, low-risk explanations answerable from supplied content need no fresh search. A local-only task does not require an invented external-tool claim or web lookup. Honor the user's explicit search request or restriction within its stated scope.

For a claim about what a specification requires, inspect the relevant official source; limit the claim to that definition. For current, version-specific, disputed or high-risk behavior, obtain evidence applicable to the claimed version and environment. Official documentation establishes intended behavior, not successful runtime execution. Use an authorized direct observation, reproducible result or relevant primary report to establish observed behavior. Preserve the distinction when the specification and observation disagree.

Choose a search or runtime check to resolve a named uncertainty that can change the conclusion or next action. Relevant issue reports and community observations can expose regressions or competing explanations; use them when they bear on that uncertainty, and inspect their original evidence before relying on them. No query count, mandatory site list or search-result total establishes support. Repeated descriptions of the same report are not independent observations.

Expand the investigation when contrary evidence challenges the current interpretation; narrow or qualify the claim when the necessary observation is unavailable. Keep the criterion unresolved when the permitted evidence cannot settle it.

## Original sources and locators

The assistant binds quoted sources, numeric claims, original evidence, tables/figures and attached files to accessible original source locations. A search snippet, summary, identifier or matching hash does not authenticate the meaning of the source. Follow the main skill's claim review and whole-response review before endorsing the answer.

Source-locator contract:

- Web evidence includes `accessible_url` and the relevant section or observation when needed to locate the claim.
- Attached or local file evidence includes `file_path`, `page` and `region`.
- `region` values are `top`, `middle`, `bottom` or `n/a`.
- Materials without pages use `page: n/a` plus an equivalent locator such as section, row, slide or sheet in `locator_note`.
- Numeric claims, original sources, tables and figures bind the specific value to its source location.
- An inaccessible source remains unconfirmed; do not substitute a secondary summary and call it the original observation.

When web evidence is used, retain the actual observation and its locator in the claim evidence. The following evidence block illustrates one observation; include only the entries needed for the claims, without a minimum count. Omit `query` when the assistant accessed a known source directly.

```text
- web-search-evidence:
  - query: <actual query, when used>
    accessible_url: <original source URL>
    finding: <observation and its relevance to the claim>
    verified_at: <original verification time>
    source-locator:
      source_type: web
      region: n/a
      locator_note: <section or relevant observation>
```

A missing required source or unresolved runtime contradiction limits the claim, regardless of how many searches succeeded. Record the supported scope and the unresolved condition; do not infer runtime behavior from official wording or a completed evidence form.
