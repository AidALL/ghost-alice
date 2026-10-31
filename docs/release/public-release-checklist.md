# Public Release Checklist

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/release/public-release-checklist.md)

Use this checklist to release the current Ghost-ALICE Core repository and its public documentation. Keep the existing repository history and prepare only the authorized release scope. This page defines release requirements; it does not attest that a particular candidate has passed them.

## Contents

- [Release Scope](#release-scope)
- [Documentation And Public Surfaces](#documentation-and-public-surfaces)
- [Source Verification](#source-verification)
- [Installed Behavior Gates](#installed-behavior-gates)
- [License And Private Material](#license-and-private-material)
- [Publication](#publication)

## Release Scope

- Identify the candidate commit and include the reviewed Core, installer, public skills, schemas, tests and documentation that belong to this release. Preserve unrelated local changes.
- Keep `VERSION` and the current compatibility records consistent. Core and addons are separate packages; a Core source update does not publish or install an addon.
- Keep `.tmp/`, local worktrees, install reports, session records, credentials, private addons, tenant material and generated caches outside the public artifact. Do not include installed home-directory copies or local agent settings.
- Distinguish source preparation, local installation, native host adoption, integration and public publication. Evidence for one does not establish the others.

## Documentation And Public Surfaces

- Synchronize `README.md`/`README_ko.md`, architecture, paired `docs/`/`docs/ko/`, Wiki pages and the English/Korean GitHub Pages entrypoints in the same release scope.
- Describe current responsibilities, commands and limits in documentation, Wiki and Pages. The [pull request expectations](../../CONTRIBUTING.md#pull-request-expectations) keep change history in commit messages only.
- Preserve literal command, path, hook, schema and enum tokens across languages. Check counterpart links and generated public surfaces against their owning catalogs.
- Keep repository docs authoritative for implementation contracts. The Wiki provides paired explanation and recovery guidance; Pages provides the public entrypoint. Publish their current meaning consistently.
- Keep `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, the code of conduct and issue/PR templates consistent with actual project policy.

## Source Verification

Build the verification plan from the candidate's affected contracts. Reuse unchanged proof under the [evidence reuse rule](../../architecture.md#evidence-and-native-delivery). Missing required coverage remains unresolved.

The [validation commands](../../CONTRIBUTING.md#validation-commands) and the [CI commands](../policies/installer-platform-compatibility-matrix.md#ci-commands) list the source checks. Also run the prose wrapping check on the candidate:

```bash
python3 scripts/check_prose_wrapping.py
```

Use the focused compatibility groups for affected installer behavior. A release that changes shared runtime or scripts needs the applicable coverage from `shared-all` and `scripts-all`; use the full compatibility matrix when the change spans its contracts. Consult the [installer compatibility matrix](../policies/installer-platform-compatibility-matrix.md) for test owners. Passing static checks does not establish native firing or model behavior.

## Installed Behavior Gates

For an installed-runtime claim, retain candidate installation provenance and actual native hook observations from the [live smoke procedure](../policies/live-smoke-regression.md). Hook configuration on disk, native trust and the running host's adoption are separate; use the [reload recovery guide](../getting-started/troubleshooting.md#native-hook-trust-and-reload) when needed.

The [verification layers](../policies/live-smoke-regression.md#verification-layers) separate governance smoke from the blind behavior gate. In a disposable environment with the candidate installed through its canonical installer and its installer-owned manifest present, use the fresh-session controller for each claimed installed subject. Replace the placeholders with release-owned case and evaluator paths; keep evaluator-private material outside the subject command and environment.

```bash
python3 -m _shared.blind_behavior --case <sealed-held-out-case.json> --platform claude --subject-command '["claude"]' --evaluator-command '["<trusted-evaluator>"]' --record .tmp/release/blind-claude.json
python3 -m _shared.blind_behavior --case <sealed-held-out-case.json> --platform codex --subject-command '["codex"]' --evaluator-command '["<trusted-evaluator>"]' --record .tmp/release/blind-codex.json
```

Required records must have exit code zero, `verdict=pass`, the intended installed platform provenance and the controller-computed suite digest. The [blind behavior record](../policies/live-smoke-regression.md#blind-behavior-record) lists what a record may store. One platform's pass does not cover another. The [automated verification boundary](../policies/live-smoke-regression.md#automated-verification-boundary) sets what automated blind screening cannot replace. Unchanged evidence follows the [evidence reuse rule](../../architecture.md#evidence-and-native-delivery); absent or affected evidence must be obtained before making its release claim.

## License And Private Material

- Confirm `LICENSE` contains the unmodified Apache License, Version 2.0 text and both READMEs agree with `NOTICE` and `THIRD_PARTY_NOTICES.md` on the license boundary.
- Included third-party material requires a source URL, license, copyright notice and local path. Keep original project commentary with short citations instead of copied upstream documentation snapshots.
- Do not imply a CLA, DCO, ownership rule or approval process that maintainers do not enforce. `CODEOWNERS`, if present, names real users or teams with the required access.
- Route vulnerability reports through `SECURITY.md` or private vulnerability reporting, not public issues.
- Review the candidate artifact for secrets and private material. Search hits in policy text and credential-helper examples require context; a keyword alone is not a leak verdict.

## Publication

1. Integrate only the reviewed release scope through the repository's existing branch and review process. Do not initialize a replacement repository or rewrite history as a release step.
2. Confirm the required source, installation and behavior evidence covers the candidate and record any unsupported platform or scope explicitly.
3. Publish the authorized commit, version tag and release artifact. Release descriptions state the current package behavior, installation, validation and limitations; [change history](../../CONTRIBUTING.md#pull-request-expectations) belongs only in commit messages.
4. Publish the paired Wiki and GitHub Pages content from their reviewed sources and confirm that the published pages match the intended candidate.
5. Check the actual CI and publication results. A local commit, queued deployment or successful upload request is not proof of visible publication.
6. When distribution or installer paths changed, use a clean clone in a disposable account or machine for the affected public-install check. Preserve unrelated development installations.
