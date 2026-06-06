# Skill Official Spec Compliance Verification Checklist

This document defines the verification process that must be passed after writing or modifying a skill. There are no exceptions.
When you create a new skill or modify an existing skill, you must pass this entire checklist before moving on to the testing stage.

Basis documents
- AGENTS.md is the project skill writing and modification contract.
- Each skill's SKILL.md is the actual applied surface for progressive disclosure, frontmatter, and reference structure.
- external specs are referenced only with an upstream link and a short citation, never by copying the original text

---

At the end of each item, an "automatic" / "manual" label distinguishes whether `scripts/validate_skills.py` runs it. For items that are not automated, you must verify them directly in PR review or a manual check.
## Contents

- [Phase 1: Frontmatter verification](#phase-1-frontmatter-verification)
- [Phase 2: SKILL.md body verification](#phase-2-skillmd-body-verification)
- [Phase 3: Structure and pattern verification](#phase-3-structure-and-pattern-verification)
- [Phase 4: References and accessory file verification](#phase-4-references-and-accessory-file-verification)
- [Phase 5: Language/tone verification (Ghost-ALICE OS project only)](#phase-5-languagetone-verification-ghost-alice-os-project-only)
- [Verification execution procedure](#verification-execution-procedure)
- [Violation severity](#violation-severity)


## Phase 1: Frontmatter verification

| # | Item | Spec criterion | Verification method | Automatic |
| --- | --- | --- | --- | :---: |
| 1-1 | `name` field exists | required | check whether the name key is present in frontmatter | automatic |
| 1-2 | `name` format | 1-64 chars, lowercase + digits + hyphens only, cannot start/end with or repeat a hyphen | regex: `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`, length ≤64 | automatic |
| 1-3 | `name` = directory name | must exactly match the parent directory name | `basename $(dirname SKILL.md)` == name value | automatic |
| 1-4 | `description` field exists | required | check whether the description key is present in frontmatter | automatic |
| 1-5 | `description` length | maximum 1024 chars, recommended within 250 chars | measured with `wc -c`. WARNING when over 250 chars, ERROR when over 1024 chars | automatic |
| 1-6 | `description` content | includes both "what it does" and "when it triggers" | a meaning judgment, so it cannot be automated | manual |
| 1-7 | `compatibility` field | required when there is an environment dependency (Python, CUDA, etc.) | whether it exceeds 500 chars is automatic, whether it is "needed but missing" is manual | partially automatic |

---

## Phase 2: SKILL.md body verification

| # | Item | Spec criterion | Verification method | Automatic |
| --- | --- | --- | --- | :---: |
| 2-1 | body line count | recommended within 500 lines | `wc -l` (excluding frontmatter) | automatic |
| 2-2 | body token count | recommended under ~5000 tokens | approximate estimate of line count × 10 | automatic |
| 2-3 | split into references/ when over 500 lines | spec guidance | substituted by the 2-1 WARNING signal | manual |
| 2-4 | no placeholders | "to be written", "TODO", "TBD", etc. are prohibited | `PLACEHOLDER_PATTERN` regex | automatic |
| 2-5 | Progressive Disclosure | split long code and tables into references/ | WARNING when a single code block in the SKILL.md body exceeds 50 lines | automatic |

---

## Phase 3: Structure and pattern verification

| # | Item | Spec criterion | Verification method | Automatic |
| --- | --- | --- | --- | :---: |
| 3-1 | Critical Rules/Gotchas section | an agent-misuse defense section exists | detect keywords in headers such as critical, prohibited, caution, failure mode, rationalization, red signal, troubleshoot | automatic |
| 3-2 | default tools/libraries stated | a concrete default rather than a "choose A or B" menu | a meaning judgment, so it cannot be automated | manual |
| 3-3 | output format template | defines the format of the output the agent will generate | a meaning judgment, so it cannot be automated | manual |
| 3-4 | reference file references | SKILL.md explicitly points to references/*.md | WARNING for suspected orphan when a file exists in the directory but its filename does not appear in the body | automatic |

---

## Phase 4: References and accessory file verification

| # | Item | Spec criterion | Verification method | Automatic |
| --- | --- | --- | --- | :---: |
| 4-1 | TOC in files over 300 lines | a table of contents is required for large files | ERROR when line count > 300 and a `## Table of Contents`, `## Contents`, or `## TOC` heading is absent | automatic |
| 4-2 | relative-path references inside files | absolute paths are prohibited | detect an absolute path with a file extension in `](...)` links inside references/*.md | automatic |
| 4-3 | scripts/ can run standalone | dependencies documented | WARNING when a keyword such as "dependency" or `requires`/`dependencies`/`depend` is absent within the top 2000 chars of each .py file | automatic |

---

## Phase 5: Language/tone verification (Ghost-ALICE OS project only)

| # | Item | Criterion | Verification method | Automatic |
| --- | --- | --- | --- | :---: |
| 5-1 | plain declarative unification | use the plain declarative style | detection of casual conversational endings in the body is not implemented (high false-positive rate) | manual |
| 5-2 | no mixed honorific style | honorific endings are prohibited | detected in the body with the `JONDAEMAL_PATTERN` regex | automatic |
| 5-3 | no casual style | casual conversational endings are prohibited | same as 5-1 (not implemented) | manual |

---

## Verification execution procedure

```
1. Parse the SKILL.md frontmatter and check all Phase 1 items
2. Count the lines of the SKILL.md body and check Phase 2
3. Scan the SKILL.md structure and check Phase 3
4. Check Phase 4 against accessory files such as references/ and scripts/
5. Run the Phase 5 language verification against all .md files
6. Compile the violating items into a list and report to the user
7. Re-verify after fixing the violating items
8. Proceed to the testing stage once all items pass
```

---

## Violation severity

| Severity | Definition | Example | Action |
|--------|------|------|------|
| ERROR | violation of a required spec condition | name mismatch, description missing, over 1024 chars | immediate fix required |
| WARNING | violation of a recommendation | description over 250 chars, body over 500 lines | fix recommended, exception allowed when there is a reason |
| INFO | improvement possible | TOC incomplete, no output template | supplement in the next iteration |
